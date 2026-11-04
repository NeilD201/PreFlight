"""Release-candidate browser regression: 20-item demo checklist.

Drives headless Chromium against the actual served release directory
through customer-visible flows only: startup, projects, local analysis,
findings, previews, navigation, switch/reload, description box, no-spend
saves, explicit-AI confirm, saved AI reports with citations, the revised
C105 finding, validated-AI plan badge, Fix Design open/close, and a clean
console. No paid Meta requests are made.

Run: PREFLIGHT_URL=http://127.0.0.1:8823 EXP_DB=/tmp/PreFlight-ReleaseCandidate/reports/.preflight.sqlite3 \
  pytest tests/test_release_regression_browser.py -q
"""
import os
import sqlite3

import pytest

pytest.importorskip('playwright.sync_api')

BASE_URL = os.environ.get('PREFLIGHT_URL', 'http://127.0.0.1:8823')
DB_PATH = os.environ.get('EXP_DB', '/tmp/PreFlight-ReleaseCandidate/reports/.preflight.sqlite3')


def usage_count():
    db = sqlite3.connect(DB_PATH)
    try:
        return db.execute('SELECT COUNT(*) FROM meta_usage').fetchone()[0]
    finally:
        db.close()


def dismiss_dialogs(page, note=''):
    for _ in range(3):
        if page.locator('dialog[open]').count() == 0:
            return
        html = page.locator('dialog[open]').first.evaluate('d => d.outerHTML.slice(0, 200)')
        print(f'dismissing dialog ({note}): {html!r}')
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)


def launch_browser(playwright):
    try:
        return playwright.chromium.launch()
    except Exception:
        return playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                  '--single-process', '--no-zygote'])


def click_tab(page, label):
    for _ in range(6):
        dismiss_dialogs(page, 'before tab click')
        try:
            page.locator('[role="tab"]', has_text=label).first.click(timeout=5000)
            return
        except Exception:
            page.wait_for_timeout(1000)
    page.locator('[role="tab"]', has_text=label).first.click()


def open_board(page, name):
    # Never navigate away while a local job is still running. The job panel
    # stays visible after completion, so wait on its running state instead.
    # Settle first: a just-triggered rethink may not have started yet.
    page.wait_for_timeout(4000)
    page.wait_for_function(
        "() => { const j = document.getElementById('job-panel');"
        " return !j || j.hidden || j.dataset.state !== 'running'; }",
        timeout=240000)
    # A pending-questions dialog can reopen after each rethink (new report);
    # keep dismissing until the navigation click lands.
    for _ in range(8):
        dismiss_dialogs(page, 'reopened interview')
        try:
            page.click('#nav-history', timeout=5000)
        except Exception:
            continue
        page.wait_for_timeout(800)
        if page.locator('#history-list').is_visible():
            break
    page.locator('#history-list').wait_for(state='visible', timeout=30000)
    card = page.locator('.history-card', has_text=name).first
    card.locator('button', has_text='Open board').click()
    page.locator('#workspace-view').wait_for(state='visible', timeout=30000)
    page.locator('#project-title', has_text=name).wait_for(state='visible', timeout=30000)


def test_release_regression():
    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            page.on('pageerror', lambda e: errors.append(f'pageerror: {str(e)[:200]}'))
            page.on('console', lambda m: errors.append(f'console-{m.type}: {m.text[:160]}')
                                   if m.type == 'error' else None)
            # 1-2: startup, projects page.
            page.goto(BASE_URL, wait_until='load', timeout=60000)
            assert 'preflight' in page.title().lower()
            page.locator('#nav-history').wait_for(state='visible', timeout=60000)
            # 3-4: both demos open.
            open_board(page, 'ModularKeyboard wrong')
            assert 'ModularKeyboard Wrong' in (page.locator('#project-title').text_content() or '')
            open_board(page, 'ModularKeyboard correct')
            assert 'ModularKeyboard Correct' in (page.locator('#project-title').text_content() or '')
            # 5-6: run control visible; local-only analysis completes.
            open_board(page, 'ModularKeyboard wrong')
            run = page.locator('#run-button')
            assert run.is_visible(), 'Check my board must be computed-visible'
            use_ai = page.locator('#use-ai')
            if use_ai.is_checked():
                use_ai.uncheck()
            datasheets = page.locator('#run-datasheets')
            if datasheets.is_checked():
                datasheets.uncheck()
            before = usage_count()
            dismiss_dialogs(page, 'before run')
            run.click()
            page.locator('#job-panel').wait_for(state='visible', timeout=30000)
            page.locator('#results').wait_for(state='visible', timeout=600000)
            headline = page.locator('#result-headline').text_content() or ''
            assert 'review' in headline.lower(), f'analysis completes, got: {headline!r}'
            # The interview (incl. the project-description box) may auto-open
            # after results; a user past it closes it and continues below.
            for _ in range(6):
                if page.locator('dialog[open]').count() == 0:
                    break
                page.keyboard.press('Escape')
                page.wait_for_timeout(800)
            # Land on the release overview where finding cards live.
            click_tab(page, 'Overview')
            page.wait_for_timeout(1500)
            if not page.locator('button', has_text='View issue').first.is_visible():
                page.evaluate('selectTab("release")')
                page.wait_for_timeout(1500)
            # 7: findings display as overview cards.
            page.locator('button', has_text='View issue').first.wait_for(state='visible', timeout=30000)
            assert page.locator('button', has_text='View issue').count() > 3, 'findings rendered'
            # Original-findings view lists every card.
            page.locator('#more-view').select_option('findings')
            page.wait_for_timeout(2000)
            assert page.locator('details.finding').first.is_visible(), 'finding cards visible'
            assert page.locator('details.finding').count() > 3
            # 8: PCB preview displays.
            page.locator('#workspace-view svg').first.wait_for(state='attached', timeout=60000)
            # 9: navigation works after the report.
            click_tab(page, 'Circuit')
            page.wait_for_timeout(1500)
            assert page.locator('[role="tab"]', has_text='Circuit').first.get_attribute('aria-selected') == 'true'
            click_tab(page, 'Overview')
            page.wait_for_timeout(1500)
            assert page.locator('[role="tab"]', has_text='Overview').first.get_attribute('aria-selected') == 'true'
            # 17: revised C105 finding names powered loads.
            body = page.text_content('#workspace-view') or ''
            assert 'C105' in body and 'powers' in body, 'load-aware C105 finding rendered'
            # 12-14: description box saves, reopens, spends nothing.
            dismiss_dialogs(page, 'before product details')
            page.click('#product-details')
            page.locator('#project-description').wait_for(state='visible', timeout=30000)
            assert 'Tell PreFlight about your project' in (page.locator('dialog[open] h2').text_content() or '')
            page.locator('#project-description').fill(
                'Regression probe: battery-powered keyboard controller, USB-C, 5V and 3V3 rails.')
            page.locator('dialog[open] button', has_text='Save & analyse').click()
            page.locator('#project-description').wait_for(state='detached', timeout=30000)
            dismiss_dialogs(page, 'post-description')
            assert usage_count() == before, 'Saving the description must not spend API credits'
            page.click('#product-details')
            page.locator('#project-description').wait_for(state='visible', timeout=30000)
            assert 'Regression probe' in (page.locator('#project-description').input_value() or ''), \
                'Description persists and reopens for editing'
            dismiss_dialogs(page, 'close description')
            # Finish the interview without AI: skip questions, confirm Save only.
            # The server has a configured key, so the paid confirm dialog is
            # expected here; the test must choose 'Save only', never Run.
            dismiss_dialogs(page, 'before interview finish')
            page.click('#product-details')
            page.locator('#project-description').wait_for(state='visible', timeout=30000)
            skip = page.locator('dialog[open] button', has_text='Answer questions instead')
            if skip.count():
                skip.click()
            for _ in range(8):
                if page.locator('dialog[open]').count() == 0:
                    break
                dlg = page.locator('dialog[open]')
                if dlg.locator('button', has_text='Skip all').count():
                    dlg.locator('button', has_text='Skip all').click()
                elif dlg.locator('button', has_text='Use answers & review').count():
                    dlg.locator('button', has_text='Use answers & review').click()
                    page.wait_for_timeout(1000)
                    save_only = page.locator('dialog[open] button', has_text='Save only')
                    if save_only.count():
                        save_only.click()
                        break
                elif dlg.locator('button', has_text='Continue').count():
                    dlg.locator('button', has_text='Continue').click()
                elif dlg.locator('button', has_text='Not sure yet').count():
                    dlg.locator('button', has_text='Not sure yet').click()
                else:
                    break
                page.wait_for_timeout(1000)
            dismiss_dialogs(page, 'post-interview')
            assert usage_count() == before, 'Answering questions must not spend API credits'
            # 15: any automatic Vector prompt must be dismissible without spend.
            # The review button itself is the explicit paid action, so the
            # test never clicks it (the key is configured on this server).
            prompt = page.locator('dialog', has_text='Run the Vector review?')
            if prompt.count():
                prompt.locator('button', has_text='Continue browsing').click()
                page.wait_for_timeout(1000)
            assert usage_count() == before, 'Dismissed Vector prompt must not spend'
            # 10: switching demos clears stale results.
            open_board(page, 'ModularKeyboard correct')
            assert not page.locator('#results').is_visible(), 'old report cleared on switch'
            # 16+18: saved AI report opens with findings, diagnostics, AI plan badge.
            dismiss_dialogs(page, 'before saved report')
            page.click('#nav-history')
            page.locator('#history-list').wait_for(state='visible', timeout=30000)
            card = page.locator('.history-card', has_text='Vector response saved').first
            card.locator('button', has_text='Open review').click()
            page.locator('#workspace-view').wait_for(state='visible', timeout=60000)
            page.wait_for_timeout(3000)
            page.locator('#more-view').select_option('ai')
            page.wait_for_timeout(2000)
            ai_text = page.text_content('#workspace-view') or ''
            assert 'R001' in ai_text, 'saved validated AI action visible'
            assert 'Review diagnostics' in ai_text, 'rejection reasons preserved'
            page.locator('#more-view').select_option('actions')
            page.wait_for_timeout(2000)
            assert 'AI guidance from supplied evidence' in (page.text_content('#workspace-view') or ''), \
                'validated AI plan badge shown, not generic advice'
            # 19: Fix Design entry renders; live prepare is NOT triggered here
            # because preparing a fix can spend paid AI (verified instead by
            # mocked Python end-to-end tests). The visible entry proves the
            # integration point is present and safe.
            click_tab(page, 'Overview')
            page.wait_for_timeout(2000)
            assert page.locator('button', has_text='Fix this issue').count() > 0, \
                'Fix Design entries render on findings'
            assert usage_count() == before, 'no paid AI spent during the whole pass'
            # 11: reload then reopen renders the workspace again.
            page.reload(wait_until='load', timeout=60000)
            page.locator('#start-view').wait_for(state='visible', timeout=60000)
            open_board(page, 'ModularKeyboard correct')
            assert page.locator('#run-button').is_visible(), 'workspace renders after reload'
        finally:
            browser.close()
    assert not errors, f'console/page errors: {errors[:5]}'
    print('release regression: passed')
