"""Audit item 1: real Chromium saves only temporary synthetic profile settings.

RED routes the committed audit JS; GREEN serves the current working copy. HTTP
failure/delay injection occurs at the request boundary; successful saves reach
the real backend and are checked against its temporary INI file.
"""
from pathlib import Path
import argparse
import configparser
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
ENDPOINT = "**/api/system/update-status"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["red", "green"])
    phase = parser.parse_args().phase
    result = dict(phase=phase, checks=[], cases=[], errors=[])

    def check(name, value, detail=None):
        result["checks"].append(dict(name=name, passed=bool(value), detail=detail))

    baseline = subprocess.check_output(
        ["git", "show", "dc51d7e:frontend/dist/update_status.js"], cwd=ROOT).decode()
    with tempfile.TemporaryDirectory(prefix="onair-settings-31841-") as tmp:
        base = Path(tmp)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                                        args=["--no-sandbox"])
            for width, height in [(1920, 1080), (1366, 768), (1024, 768)]:
                runtime = None
                try:
                    shared = base / str(width) / "shared"
                    shared.mkdir(parents=True)
                    sqlite3.connect(shared / "smart_duty.db").close()
                    runtime = Runtime(base / str(width), "admin", "ADMIN", shared)
                    runtime.seed()
                    config = runtime.profile / "config/config_admin.ini"

                    def file_settings():
                        cfg = configparser.ConfigParser()
                        cfg.read(config)
                        return dict(cfg["NETWORK"])

                    context = browser.new_context(viewport=dict(width=width, height=height),
                                                  locale="ko-KR", timezone_id="Asia/Seoul")
                    if phase == "red":
                        context.route("**/update_status.js*", lambda route: route.fulfill(
                            status=200, content_type="application/javascript", body=baseline))
                    page = context.new_page()
                    page.on("pageerror", lambda error: result["errors"].append(str(error)))
                    page.goto(runtime.url + "/#onair_access=" + runtime.tickets.pop())
                    page.wait_for_function("dataLoaded && memberList.length === 12")
                    page.evaluate("openServerInfo()")
                    page.locator("#set-port").wait_for(state="attached")
                    page.locator("#update-settings summary").click()
                    page.evaluate("document.fonts.ready")
                    port, hosts, save = [page.locator(selector) for selector in ["#set-port", "#set-hosts", "#set-save"]]
                    out = page.locator("#update-action-result")
                    initial = port.input_value()
                    posts, snapshots = [], []
                    state = dict(mode="normal", held=None)

                    def route_settings(route):
                        if route.request.method != "POST":
                            route.continue_()
                            return
                        body = route.request.post_data_json
                        if body.get("action") != "save_settings":
                            route.continue_()
                            return
                        posts.append(body)
                        if state["mode"] == "fail-once":
                            state["mode"] = "normal"
                            route.fulfill(status=503, content_type="application/json",
                                          body=json.dumps(dict(detail="검증용 일시적인 저장 실패")))
                        elif state["mode"] == "hold":
                            if state["held"] is None:
                                state["held"] = (route, route.fetch())
                            else:
                                route.fulfill(status=503, content_type="application/json",
                                              body=json.dumps(dict(detail="검증용 중복 요청 차단")))
                        else:
                            route.continue_()

                    page.route(ENDPOINT, route_settings)

                    def submit():
                        save.focus()
                        page.keyboard.press("Enter")
                        page.wait_for_function("document.querySelector('#update-action-result').textContent !== '저장 중…'")

                    def snapshot(step):
                        item = dict(step=step, port=port.input_value(), hosts=hosts.input_value(),
                                    file=file_settings(), message=out.inner_text(), posts=list(posts))
                        snapshots.append(item)
                        return item

                    port.fill("18234")
                    submit()
                    first = snapshot("A-to-B")
                    check(f"{width} keyboard save writes B", first["file"]["server_port"] == "18234", first)
                    port.fill(initial)
                    submit()
                    restored = snapshot("B-to-A-same-window")
                    check(f"{width} same-window restore writes A", restored["file"]["server_port"] == initial, restored)
                    check(f"{width} restore sends second request", len(posts) == 2, restored)
                    page.screenshot(path=str(OUT / f"settings-{phase}-{width}-restore.png"), animations="disabled")

                    before_failure = file_settings()
                    state["mode"] = "fail-once"
                    port.fill("18235")
                    hosts.fill("retry-office")
                    submit()
                    failed = snapshot("injected-failure")
                    check(f"{width} failed save retains inputs", failed["port"] == "18235" and failed["hosts"] == "retry-office", failed)
                    check(f"{width} failed save leaves file untouched", failed["file"] == before_failure, failed)
                    check(f"{width} failed save re-enables retry", save.is_enabled(), failed)
                    submit()
                    retry = snapshot("real-retry")
                    check(f"{width} retry reaches real file", retry["file"]["server_port"] == "18235" and retry["file"]["allowed_hosts"] == "retry-office", retry)

                    state["mode"] = "hold"
                    port.fill("18236")
                    hosts.fill("submitted-office")
                    save.focus()
                    page.keyboard.press("Enter")
                    page.wait_for_function("document.querySelector('#update-action-result').textContent === '저장 중…'")
                    # Pump the network route while its real response is deliberately withheld.
                    page.wait_for_timeout(50)
                    check(f"{width} save disabled during request", not save.is_enabled())
                    count = len(posts)
                    port.fill("18237")
                    hosts.fill("typed-office")
                    page.evaluate("void document.querySelector('#set-save').onclick()")
                    page.wait_for_timeout(50)
                    check(f"{width} concurrent handler sends no second save", len(posts) == count, dict(posts=list(posts)))
                    held_route, response = state["held"]
                    state["mode"] = "normal"
                    held_route.fulfill(response=response)
                    page.wait_for_function("document.querySelector('#update-action-result').textContent.startsWith('저장했습니다')")
                    pending = snapshot("success-after-further-input")
                    check(f"{width} submitted snapshot reaches file", pending["file"]["server_port"] == "18236" and pending["file"]["allowed_hosts"] == "submitted-office", pending)
                    check(f"{width} success preserves newer draft", pending["port"] == "18237" and pending["hosts"] == "typed-office", pending)
                    submit()
                    final = snapshot("save-newer-draft")
                    check(f"{width} newer draft remains unsaved until next submit", final["file"]["server_port"] == "18237" and final["file"]["allowed_hosts"] == "typed-office", final)
                    page.screenshot(path=str(OUT / f"settings-{phase}-{width}-draft.png"), animations="disabled")
                    geometry = page.locator("#serverinfo-modal .modal-body").evaluate("n=>({width:n.clientWidth,scrollWidth:n.scrollWidth})")
                    check(f"{width} dialog has no horizontal overflow", geometry["scrollWidth"] <= geometry["width"] + 1, geometry)
                    result["cases"].append(dict(width=width, snapshots=snapshots))
                    context.close()
                except Exception:
                    result["errors"].append(traceback.format_exc())
                finally:
                    if runtime:
                        runtime.close()
            browser.close()
    result["summary"] = dict(passed=sum(x["passed"] for x in result["checks"]),
                             failed=sum(not x["passed"] for x in result["checks"]), errors=len(result["errors"]))
    (OUT / f"settings-{phase}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"]))
    for error in result["errors"]:
        print(error)
    return 1 if result["errors"] or (phase == "green" and result["summary"]["failed"]) else 0


if __name__ == "__main__":
    sys.exit(main())
