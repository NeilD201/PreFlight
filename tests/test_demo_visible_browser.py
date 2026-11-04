"""Real-browser regression: the demo run control must be visibly rendered.

Drives headless Chromium against a live server through the exact user flows
that previously showed a blank workspace (bundled demo open, + New project
then open, switch after a saved report, reload). Asserts computed CSS
visibility of Check my board — not just DOM presence — then clicks it and
verifies a local-only analysis starts and completes. No Meta calls are made
(AI review stays off and datasheet lookups are unchecked).

Run: PREFLIGHT_URL=http://127.0.0.1:8814 pytest tests/test_demo_visible_browser.py -q
"""
import os

import pytest

pytest.importorskip('playwright.sync_api')

BASE_URL = os.environ.get('PREFLIGHT_URL', 'http://127.0.0.1:8814')
RUN_BUTTON = '#run-button'


def open_demo(page, name):
    dismiss_dialogs(page, 'before Projects navigation')
    page.click('#nav-history')
    page.locator('#history-list').wait_for(state='visible', timeout=30000)
    card = page.locator('.history-card', has_text=name).first
    card.locator('button', has_text='Open board').click()
    page.locator('#workspace-view').wait_for(state='visible', timeout=30000)


def dismiss_dialogs(page, note=''):
    # A real user past a modal question dialog presses Escape to continue
    # later; both interview dialogs finish without saving or re-running.
    # Logging what was dismissed keeps a blocking regression visible.
    for _ in range(3):
        if page.locator('dialog[open]').count() == 0:
            return
        html = page.locator('dialog[open]').first.evaluate('d => d.outerHTML.slice(0, 300)')
        print(f'dismissing dialog ({note}): {html!r}')
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)


def assert_run_control_visible(page, label='Check my board'):
    run = page.locator(RUN_BUTTON)
    assert run.is_visible(), 'Check my board must be computed-visible, not just in the DOM'
    assert label in (run.text_content() or ''), 'run control keeps its real label'
    return run


def launch_browser(playwright):
    try:
        return playwright.chromium.launch()
    except Exception:
        # Restricted sandboxes kill Chromium's multiprocess IPC; single
        # process renders the same DOM/CSS for visibility assertions.
        return playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                  '--single-process', '--no-zygote'])


def test_demo_run_control_visible_and_starts_local_analysis():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            page.goto(BASE_URL, wait_until='load', timeout=60000)
            page.locator('#nav-history').wait_for(state='visible', timeout=60000)

            # Both demos open from Projects with a visible run control.
            for name, title in (('ModularKeyboard wrong', 'ModularKeyboard Wrong'),
                                ('ModularKeyboard correct', 'ModularKeyboard Correct')):
                open_demo(page, name)
                assert_run_control_visible(page)
                assert title in (page.locator('#project-title').text_content() or '')

            # + New project, then open a demo: the workspace must render.
            page.click('#nav-history')
            page.click('#add-project')
            open_demo(page, 'ModularKeyboard wrong')
            assert_run_control_visible(page)

            # Local-only analysis: force AI review off and datasheet lookups
            # off regardless of server defaults, so the run spends nothing.
            use_ai = page.locator('#use-ai')
            if use_ai.is_checked():
                use_ai.uncheck()
            assert not use_ai.is_checked()
            datasheets = page.locator('#run-datasheets')
            if datasheets.is_checked():
                datasheets.uncheck()
            assert not datasheets.is_checked()
            dismiss_dialogs(page, 'before run')
            assert page.locator('dialog[open]').count() == 0
            assert_run_control_visible(page).click()
            page.locator('#job-panel').wait_for(state='visible', timeout=30000)
            run = page.locator(RUN_BUTTON)
            assert not run.is_enabled() or 'Checking' in (run.text_content() or ''), \
                'clicking Check my board starts the local check'
            page.locator('#results').wait_for(state='visible', timeout=600000)
            headline = page.locator('#result-headline').text_content() or ''
            assert 'review' in headline.lower(), f'local analysis completes with findings, got: {headline!r}'
            dismiss_dialogs(page, 'post-analysis')

            # Switching demos after a saved report clears the old results.
            open_demo(page, 'ModularKeyboard correct')
            assert_run_control_visible(page)
            assert not page.locator('#results').is_visible(), 'old report is cleared on demo switch'
            assert 'ModularKeyboard Correct' in (page.locator('#project-title').text_content() or '')

            # A reload boots to Start by design; reopening the demo from
            # Projects must render the workspace with a visible run control.
            page.reload(wait_until='load', timeout=60000)
            page.locator('#start-view').wait_for(state='visible', timeout=60000)
            open_demo(page, 'ModularKeyboard correct')
            assert_run_control_visible(page)
        finally:
            browser.close()
    print('demo visible browser: passed')
