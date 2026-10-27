"""The demo's Confirmation flow, driven in a browser: Identity Verification, a Lost or Stolen
Report, and its Confirmation in the Customer's app panel (ADR-0004).

Shared by the GIF recorder and the end-to-end test of the demo page. It asserts the flow
succeeded, so a recording that finishes is also a passing check.
"""

from playwright.async_api import Locator, Page, expect

OPENING = (
    "Hi, I've lost my debit card for good and want it cancelled and replaced. I'm Ana Souza: "
    "customer number C-1001, born 1990-05-17, postcode SW1A 1AA."
)
NUDGES = (
    "Yes, I understand the fee. Please go ahead.",
    "Yes, please request the cancellation now.",
)
REPLY_TIMEOUT = 90_000


async def confirmation_flow(page: Page, url: str) -> None:
    await page.goto(url)
    await page.get_by_role("button", name="New conversation").click()
    await say(page, OPENING)
    confirm = page.get_by_role("button", name="Confirm")
    for nudge in NUDGES:
        if await _appears(confirm):
            break
        await say(page, nudge)
    await expect(confirm).to_be_visible(timeout=10_000)
    await page.wait_for_timeout(1_500)
    await confirm.first.click()
    await expect(page.get_by_text("Confirmed.")).to_be_visible()
    await say(page, "Thank you!")


async def say(page: Page, text: str) -> None:
    """Sends a message and waits for the agent's whole reply."""
    replies = page.locator(".bubble.agent")
    before = await replies.count()
    await page.get_by_label("Your message").fill(text)
    await page.get_by_role("button", name="Send").click()
    await expect(replies).to_have_count(before + 1)
    await expect(page.get_by_role("button", name="Send")).to_be_enabled(timeout=REPLY_TIMEOUT)
    await expect(replies.last).not_to_have_class("bubble agent error")


async def _appears(confirm_button: Locator) -> bool:
    """Whether the app panel shows a request within one of its refreshes."""
    try:
        await expect(confirm_button.first).to_be_visible(timeout=3_000)
    except AssertionError:
        return False
    return True
