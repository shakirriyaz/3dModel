"""Run every DEXPI .xml under a directory through reader + layout + routing + QC and summarise.
Usage: python scripts/run_corpus.py pilot_data"""
import collections, glob, signal, sys, time
from autoplant.io_dexpi import load_dexpi
from autoplant.pipeline import build_model


class Timeout(Exception):
    pass


def main(root):
    files = [f for f in sorted(glob.glob(f"{root}/**/*.xml", recursive=True)) if "xsd" not in f.lower()]
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(Timeout()))
    res, bad, rows = collections.Counter(), {}, []
    t0 = time.time()
    for f in files:
        signal.alarm(120)
        try:
            prj = load_dexpi(f)
        except Timeout:
            res["read_timeout"] += 1
            continue
        except Exception as e:
            res[f"no model: {str(e)[:50]}"] += 1
            signal.alarm(0)
            continue
        try:
            t = time.time()
            _, routes, rep = build_model(prj)
            res["routed + QC passed" if rep.passed else "QC errors"] += 1
            rows.append((f, len(prj.equipment), len(prj.lines), round(time.time() - t, 1)))
            if not rep.passed:
                bad[f] = collections.Counter(i.check for i in rep.items if i.severity == "error")
        except Exception as e:
            res[f"crash {type(e).__name__}"] += 1
            bad[f] = str(e)[:80]
        finally:
            signal.alarm(0)
    print(f"{len(files)} files in {time.time() - t0:.0f}s")
    for k, v in res.most_common():
        print(f"{v:5d}  {k}")
    for f, why in list(bad.items())[:20]:
        print("  FAIL", f, dict(why) if hasattr(why, "items") else why)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pilot_data")
