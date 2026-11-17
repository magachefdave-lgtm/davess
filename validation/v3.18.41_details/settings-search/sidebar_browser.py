"""Audit item 9: real Chromium sidebar geometry and keyboard preview.

The server, config and SQLite database are temporary synthetic profiles. RED
serves the committed audit CSS; GREEN serves the current working copy.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "validation/v3.18.40_ca_years"))
os.environ["ONAIR_SERVER_PYTHON"] = sys.executable
from runtime import Runtime
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent
CASES = [("normal-three", "김민수"), ("normal-four", "남궁민수"),
         ("legacy-long", "김민수테스트긴이름")]
GEOMETRY = """button => {
  const line = button.querySelector('.abs-long-line');
  const person = button.querySelector('.abs-long-person');
  const period = button.querySelector('.abs-long-period');
  const describe = n => ({text:n.textContent, rect:n.getBoundingClientRect().toJSON(),
    width:n.clientWidth, scrollWidth:n.scrollWidth, height:n.clientHeight,
    scrollHeight:n.scrollHeight, whiteSpace:getComputedStyle(n).whiteSpace,
    textOverflow:getComputedStyle(n).textOverflow, overflow:getComputedStyle(n).overflow});
  return {line:describe(line), person:describe(person), period:describe(period),
    label:button.getAttribute('aria-label'), periodTitle:period.title};
}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["red", "green"])
    args = parser.parse_args()
    evidence = {"phase": args.phase, "checks": [], "cases": [], "errors": []}
    runtime = None

    def check(name, value, detail=None):
        evidence["checks"].append(dict(name=name, passed=bool(value), detail=detail))

    try:
        with tempfile.TemporaryDirectory(prefix="onair-sidebar-31841-") as tmp:
            base = Path(tmp)
            shared = base / "shared"
            shared.mkdir()
            sqlite3.connect(shared / "smart_duty.db").close()
            runtime = Runtime(base, "admin", "ADMIN", shared)
            runtime.seed()
            baseline = subprocess.check_output(
                ["git", "show", "dc51d7e:frontend/dist/absence_management.css"], cwd=ROOT
            ).decode()
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                                            args=["--no-sandbox"])
                for width, height in [(1920, 1080), (1366, 768), (1024, 768)]:
                    for case, name in CASES:
                        context = browser.new_context(viewport=dict(width=width, height=height),
                                                      locale="ko-KR", timezone_id="Asia/Seoul")
                        if args.phase == "red":
                            context.route("**/absence_management.css*", lambda route: route.fulfill(
                                status=200, content_type="text/css", body=baseline))
                        page = context.new_page()
                        page.on("pageerror", lambda error: evidence["errors"].append(str(error)))
                        page.clock.set_fixed_time(datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc))
                        page.goto(runtime.url + "/#onair_access=" + runtime.tickets.pop())
                        page.wait_for_function("dataLoaded && memberList.length === 12")
                        page.evaluate("document.fonts.ready")
                        # Loaded legacy records remain readable independently of new-name validation.
                        page.evaluate("""name => {
                          absences=[{id:901,name,type:'파견',start:'2026-10-01',end:'2027-03-31'}];
                          openAbsenceModal();
                        }""", name)
                        button = page.locator('#abs-long-list [data-absence-preview="901"]')
                        button.wait_for()
                        button.scroll_into_view_if_needed()
                        item = page.locator('#abs-long-list [data-absence-id="901"]')
                        item.screenshot(path=str(OUT / f"sidebar-{args.phase}-{width}-{case}.png"),
                                        animations="disabled")
                        geom = button.evaluate(GEOMETRY)
                        prefix = f"{width} {case}"
                        line, person, period = [geom[key] for key in ["line", "person", "period"]]
                        check(prefix + " period stays inside row",
                              period["rect"]["right"] <= line["rect"]["right"] + 1, geom)
                        check(prefix + " row has no horizontal clipping",
                              line["scrollWidth"] <= line["width"] + 1, geom)
                        check(prefix + " one line retained",
                              abs(person["rect"]["top"] - period["rect"]["top"]) < 2
                              and line["height"] <= 19, geom)
                        check(prefix + " final weekday readable",
                              period["text"].endswith("(수)")
                              and period["scrollWidth"] <= period["width"] + 1, geom)
                        check(prefix + " full accessible name and period retained",
                              name in geom["label"] and "2026" in geom["periodTitle"]
                              and "2027" in geom["periodTitle"], geom)
                        if case.startswith("normal"):
                            check(prefix + " normal name fully visible",
                                  person["scrollWidth"] <= person["width"] + 1, geom)
                        elif person["scrollWidth"] > person["width"] + 1:
                            check(prefix + " legacy name has ellipsis",
                                  person["textOverflow"] == "ellipsis"
                                  and person["overflow"] == "hidden", geom)
                        button.focus()
                        page.keyboard.press("Enter")
                        check(prefix + " keyboard previews full record",
                              page.locator('#abs-calendar-selection .abs-calendar-person').inner_text() == name
                              and button.get_attribute("aria-pressed") == "true")
                        check(prefix + " keyboard focus stays on preview", button.evaluate(
                            "n => document.activeElement === n"))
                        evidence["cases"].append(dict(width=width, height=height, case=case,
                                                      geometry=geom))
                        context.close()
                browser.close()
    except Exception:
        evidence["errors"].append(traceback.format_exc())
    finally:
        if runtime:
            runtime.close()
        evidence["passed"] = sum(item["passed"] for item in evidence["checks"])
        evidence["failed"] = sum(not item["passed"] for item in evidence["checks"])
        (OUT / f"sidebar-{args.phase}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps({key: evidence[key] for key in ["phase", "passed", "failed", "errors"]},
                     ensure_ascii=False))
    return 1 if evidence["errors"] or evidence["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
