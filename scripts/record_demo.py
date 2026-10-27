"""Records the demo GIF: the two-panel Confirmation flow, driven in a browser by Playwright
and converted with ffmpeg. The flow asserts it succeeded, so the GIF only exists if the demo
works.

It drives the running demo stack (`docker compose up`), whose agent is the real one on the
default model: about one cent per recording, so only the user runs it.

    uv run python scripts/record_demo.py [--url http://localhost:8000] [--out docs/demo.gif]
"""

import argparse
import asyncio
import subprocess
import tempfile
from pathlib import Path

from demo_flow import confirmation_flow
from playwright.async_api import ViewportSize, async_playwright

SIZE: ViewportSize = {"width": 1280, "height": 720}


async def record(url: str, video_dir: Path) -> Path:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport=SIZE, record_video_dir=video_dir, record_video_size=SIZE
        )
        page = await context.new_page()
        await confirmation_flow(page, url)
        await page.wait_for_timeout(2_000)
        await context.close()
        await browser.close()
        assert page.video is not None
        return Path(await page.video.path())


def to_gif(video: Path, gif: Path) -> None:
    """Two-pass palette, so the page's flat colours stay crisp."""
    filters = "fps=8,scale=960:-1:flags=lanczos"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"{filters},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse",
            str(gif),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("docs/demo.gif"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as video_dir:
        video = asyncio.run(record(args.url, Path(video_dir)))
        to_gif(video, args.out)
    print(f"Recorded {args.out}.")


if __name__ == "__main__":
    main()
