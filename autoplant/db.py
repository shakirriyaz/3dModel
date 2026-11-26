"""Multi-user project store (SQLite, stdlib only): users, sessions, projects, members, revisions,
markups, clash waivers and an audit log. File outputs live next to the DB under data_dir/projects."""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, sqlite3, threading, time
from contextlib import contextmanager

ROLES = ("viewer", "editor", "admin")
SESSION_HOURS = 12

SCHEMA = """
create table if not exists users(id integer primary key, username text unique not null, pw_hash text not null,
  role text not null default 'editor', created real not null);
create table if not exists sessions(token text primary key, user_id integer not null, expires real not null);
create table if not exists projects(id integer primary key, name text not null, owner_id integer not null,
  created real not null, current_rev integer not null default 0);
create table if not exists members(project_id integer, user_id integer, role text not null, primary key(project_id, user_id));
create table if not exists revisions(project_id integer, rev integer, author_id integer, time real, kind text,
  comment text, summary text, primary key(project_id, rev));
create table if not exists markups(id integer primary key, project_id integer, rev integer, element text, x real, y real, z real,
  text text, status text default 'open', author_id integer, created real, resolved_by integer, resolved_at real);
create table if not exists markup_comments(id integer primary key, markup_id integer, author_id integer, text text, created real);
create table if not exists clash_status(project_id integer, key text, status text, comment text, user_id integer, time real,
  primary key(project_id, key));
create table if not exists audit(id integer primary key, project_id integer, user_id integer, time real, action text, detail text);
"""


def hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def check_pw(pw: str, stored: str) -> bool:
    salt, h = stored.split("$")
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt)).split("$")[1], h)


class Store:
    def __init__(self, data_dir: str):
        self.dir = data_dir
        os.makedirs(os.path.join(data_dir, "projects"), exist_ok=True)
        self.path = os.path.join(data_dir, "autoplant.db")
        self._lock = threading.RLock()
        self._plocks: dict[int, threading.Lock] = {}
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def plock(self, pid: int) -> threading.Lock:
        with self._lock:
            return self._plocks.setdefault(pid, threading.Lock())

    def project_dir(self, pid: int) -> str:
        return os.path.join(self.dir, "projects", str(pid))

    # ---------------- users / sessions ----------------
    def user_count(self) -> int:
        with self.conn() as c:
            return c.execute("select count(*) from users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str = "editor") -> dict:
        if role not in ROLES:
            raise ValueError("bad role")
        if len(password) < 8 or not username.strip():
            raise ValueError("username required and password must be at least 8 characters")
        with self.conn() as c:
            try:
                cur = c.execute("insert into users(username, pw_hash, role, created) values(?,?,?,?)",
                                (username.strip(), hash_pw(password), role, time.time()))
            except sqlite3.IntegrityError:
                raise ValueError("username already exists")
            return {"id": cur.lastrowid, "username": username.strip(), "role": role}

    def login(self, username: str, password: str) -> tuple[str, dict] | None:
        with self.conn() as c:
            u = c.execute("select * from users where username=?", (username.strip(),)).fetchone()
            if not u or not check_pw(password, u["pw_hash"]):
                return None
            tok = secrets.token_urlsafe(32)
            c.execute("insert into sessions values(?,?,?)", (tok, u["id"], time.time() + SESSION_HOURS * 3600))
            return tok, {"id": u["id"], "username": u["username"], "role": u["role"]}

    def user_for_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.conn() as c:
            r = c.execute("select u.id, u.username, u.role from sessions s join users u on u.id=s.user_id "
                          "where s.token=? and s.expires>?", (token, time.time())).fetchone()
            return dict(r) if r else None

    def logout(self, token: str):
        with self.conn() as c:
            c.execute("delete from sessions where token=?", (token,))

    def list_users(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("select id, username, role from users order by username")]

    def user_by_name(self, username: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("select id, username, role from users where username=?", (username,)).fetchone()
            return dict(r) if r else None

    # ---------------- projects ----------------
    def create_project(self, name: str, owner_id: int) -> int:
        with self.conn() as c:
            cur = c.execute("insert into projects(name, owner_id, created) values(?,?,?)", (name, owner_id, time.time()))
            pid = cur.lastrowid
        os.makedirs(os.path.join(self.project_dir(pid), "out"), exist_ok=True)
        return pid

    def project(self, pid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("select p.*, u.username as owner from projects p join users u on u.id=p.owner_id where p.id=?", (pid,)).fetchone()
            return dict(r) if r else None

    def role_in(self, pid: int, user: dict) -> str | None:
        """Effective role: global admin > owner(editor+) > member role."""
        p = self.project(pid)
        if p is None:
            return None
        if user["role"] == "admin":
            return "admin"
        if p["owner_id"] == user["id"]:
            return "editor"
        with self.conn() as c:
            r = c.execute("select role from members where project_id=? and user_id=?", (pid, user["id"])).fetchone()
        if r is None:
            return None
        # a global 'viewer' can never edit, whatever the project role says
        return "viewer" if user["role"] == "viewer" else r["role"]

    def projects_for(self, user: dict) -> list[dict]:
        with self.conn() as c:
            if user["role"] == "admin":
                rows = c.execute("select p.*, u.username as owner from projects p join users u on u.id=p.owner_id order by p.id desc")
            else:
                rows = c.execute("select p.*, u.username as owner from projects p join users u on u.id=p.owner_id where p.owner_id=? or "
                                 "p.id in (select project_id from members where user_id=?) order by p.id desc", (user["id"], user["id"]))
            return [dict(r) for r in rows]

    def set_member(self, pid: int, user_id: int, role: str):
        if role not in ("viewer", "editor"):
            raise ValueError("member role must be viewer or editor")
        with self.conn() as c:
            c.execute("insert or replace into members values(?,?,?)", (pid, user_id, role))

    def members(self, pid: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("select u.username, m.role from members m join users u on u.id=m.user_id where m.project_id=?", (pid,))]

    # ---------------- revisions ----------------
    def add_revision(self, pid: int, author_id: int, kind: str, comment: str, summary: dict, model_json_path: str) -> int:
        with self.conn() as c:
            rev = c.execute("select current_rev from projects where id=?", (pid,)).fetchone()[0] + 1
            c.execute("insert into revisions values(?,?,?,?,?,?,?)", (pid, rev, author_id, time.time(), kind, comment, json.dumps(summary)))
            c.execute("update projects set current_rev=? where id=?", (rev, pid))
        d = os.path.join(self.project_dir(pid), "revs", str(rev))
        os.makedirs(d, exist_ok=True)
        with open(model_json_path, "rb") as src, open(os.path.join(d, "model.json"), "wb") as dst:
            dst.write(src.read())
        return rev

    def revisions(self, pid: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("select r.*, u.username as author from revisions r left join users u on u.id=r.author_id "
                             "where r.project_id=? order by rev desc", (pid,))
            return [{**dict(r), "summary": json.loads(r["summary"] or "{}")} for r in rows]

    # ---------------- markups ----------------
    def add_markup(self, pid, rev, element, xyz, text, author_id) -> int:
        x, y, z = xyz if xyz else (None, None, None)
        with self.conn() as c:
            return c.execute("insert into markups(project_id, rev, element, x, y, z, text, author_id, created) values(?,?,?,?,?,?,?,?,?)",
                             (pid, rev, element, x, y, z, text, author_id, time.time())).lastrowid

    def markups(self, pid) -> list[dict]:
        with self.conn() as c:
            ms = [dict(r) for r in c.execute("select m.*, u.username as author from markups m left join users u on u.id=m.author_id "
                                             "where m.project_id=? order by m.id", (pid,))]
            for m in ms:
                m["comments"] = [dict(r) for r in c.execute("select c.text, c.created, u.username as author from markup_comments c "
                                                            "left join users u on u.id=c.author_id where c.markup_id=? order by c.id", (m["id"],))]
            return ms

    def comment_markup(self, pid, mid, author_id, text):
        with self.conn() as c:
            if not c.execute("select 1 from markups where id=? and project_id=?", (mid, pid)).fetchone():
                raise KeyError("markup")
            c.execute("insert into markup_comments(markup_id, author_id, text, created) values(?,?,?,?)", (mid, author_id, text, time.time()))

    def set_markup_status(self, pid, mid, status, user_id):
        if status not in ("open", "resolved"):
            raise ValueError("status must be open or resolved")
        with self.conn() as c:
            n = c.execute("update markups set status=?, resolved_by=?, resolved_at=? where id=? and project_id=?",
                          (status, user_id if status == "resolved" else None, time.time() if status == "resolved" else None, mid, pid)).rowcount
            if not n:
                raise KeyError("markup")

    # ---------------- clash waivers / audit ----------------
    def clash_status(self, pid) -> dict:
        with self.conn() as c:
            return {r["key"]: {"status": r["status"], "comment": r["comment"], "user": r["username"]}
                    for r in c.execute("select s.key, s.status, s.comment, u.username from clash_status s left join users u on u.id=s.user_id where s.project_id=?", (pid,))}

    def set_clash_status(self, pid, key, status, comment, user_id):
        with self.conn() as c:
            if status == "open":
                c.execute("delete from clash_status where project_id=? and key=?", (pid, key))
            else:
                c.execute("insert or replace into clash_status values(?,?,?,?,?,?)", (pid, key, status, comment, user_id, time.time()))

    def audit(self, pid, user_id, action, detail=""):
        with self.conn() as c:
            c.execute("insert into audit(project_id, user_id, time, action, detail) values(?,?,?,?,?)",
                      (pid, user_id, time.time(), action, detail[:2000]))

    def audit_log(self, pid, limit=200) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("select a.time, a.action, a.detail, u.username from audit a left join users u on u.id=a.user_id "
                                               "where a.project_id=? order by a.id desc limit ?", (pid, limit))]
