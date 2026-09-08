# Runbooks

Operational procedures that cannot be automated in CI and require a human on a
network-enabled host.

## LinkedIn consent-cookie validation

### Which tests require manual, network-enabled runs

- **LinkedIn consent validation** — the consent cookies deferred at
  [`src/robotsix_browser/operations.py:129`](src/robotsix_browser/operations.py)
  (`_LINKEDIN_CONSENT_COOKIES`) can only be validated against the live
  `linkedin.com` `/login` page. The hermetic tests in
  `tests/test_linkedin_fill_live.py` and `tests/test_operations.py` use
  `data:` URLs and mocked pages, so they exercise the fill logic but **cannot**
  confirm the cookie values still suppress the consent redirect.
- **Any other test that navigates to a live external site** falls under the
  same rule: run it manually with network access, never rely on hosted CI to
  validate it.

### Why they cannot run in GitHub-hosted CI

- LinkedIn serves datacenter IPs (which GitHub-hosted runners use) a
  **different consent form** — one that is missing the email input the fill
  logic expects.
- As a result the test fails **reliably** in hosted CI due to environmental
  discrimination, not a code bug.
- Running it in CI therefore wastes cycles without producing meaningful
  validation, so it must stay deferred and be validated by a human instead.

### Step-by-step manual validation

1. Install the Playwright browser:

   ```bash
   uv run playwright install chromium
   ```

2. Run locally on a host with network access to `linkedin.com` (a residential
   or non-datacenter IP — datacenter IPs get the discriminated consent form).
3. From a consenting browser session, capture the real `li_gc` and
   `OptanonConsent` cookie values (browser dev tools → Application → Cookies →
   `https://www.linkedin.com`).
4. Update `_LINKEDIN_CONSENT_COOKIES` in
   `src/robotsix_browser/operations.py` with the real values.
5. Re-run the test to confirm the `/login` load does **not** redirect to
   `/legal/cookie-policy` (see `_CONSENT_REDIRECT_PATTERN` in the same module).
6. Commit the tuned values with a note referencing this runbook.

### Reference

The docstring at
[`src/robotsix_browser/operations.py:127–129`](src/robotsix_browser/operations.py)
flags that "Values may need live tuning against the current LinkedIn consent
format." This runbook is the procedure for that tuning.
