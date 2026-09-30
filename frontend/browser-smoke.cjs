// Optional real-browser regression. Use an isolated, seeded backend (see README.md).
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.FRONTEND_TEST_URL || "http://127.0.0.1:3000";
const api = process.env.BACKEND_TEST_URL;
const fixture = process.env.VIDEO_TEST_FILE;
assert.ok(
  api && fixture,
  "Set BACKEND_TEST_URL to an isolated backend and VIDEO_TEST_FILE to a 30–60 second MP4.",
);
const output =
  process.env.SCREENSHOT_DIR || "/private/tmp/shorts-ui-screenshots";
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1050 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript((apiBaseUrl) => {
      window.SHORTS_CONFIG = { apiBaseUrl };
    }, api);
    const capture = async (name) => {
      await page.screenshot({
        path: path.join(output, `${name}.png`),
        fullPage: true,
        animations: "disabled",
      });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > window.innerWidth,
        ),
        false,
        `No page overflow: ${name}`,
      );
    };
    const nav = async (name) => {
      const trigger = page.getByRole("button", {
        name: "Open navigation",
        exact: true,
      });
      if (await trigger.isVisible()) await trigger.click();
      await page
        .getByRole("navigation", { name: "Main", exact: true })
        .getByRole("button", { name, exact: true })
        .click();
    };
    await page.goto(base, { waitUntil: "networkidle" });
    await page.getByLabel("Email address").fill("maya@example.com");
    await page.getByLabel("Password", { exact: true }).fill("DemoPass123!");
    await capture("login");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page
      .getByRole("heading", { name: "Dashboard", exact: true })
      .waitFor();
    await page.getByRole("table").waitFor();
    await capture("dashboard");
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByRole("heading", { name: "Dashboard", exact: true })
      .waitFor();
    await page.getByRole("table").waitFor();
    assert.equal(
      await page.getByLabel("Email address").count(),
      0,
      "Refresh keeps the creator signed in",
    );
    await nav("Projects");
    await page.getByRole("searchbox").fill("does-not-exist");
    await page.getByRole("heading", { name: "No matching projects" }).waitFor();
    await page.getByRole("searchbox").fill("");
    await capture("projects");
    await nav("New Project");
    await capture("new-project");
    await page
      .getByRole("button", { name: "YouTube URL", exact: true })
      .click();
    await page
      .getByLabel("YouTube video URL")
      .fill("https://www.youtube.com/watch?v=example");
    assert.equal(
      await page.getByRole("button", { name: "Find my Shorts" }).isDisabled(),
      true,
    );
    await page.getByRole("checkbox").check();
    assert.equal(
      await page.getByRole("button", { name: "Find my Shorts" }).isEnabled(),
      true,
    );
    await capture("youtube");
    await page
      .getByRole("button", { name: "Upload video", exact: true })
      .click();
    await page.getByLabel("Choose a video to upload").setInputFiles(fixture);
    await page
      .getByLabel(/^What should the AI look for/)
      .fill("Find a clear standalone lesson.");
    await capture("upload-ready");
    await page.getByRole("button", { name: "Find my Shorts" }).click();
    await page
      .getByRole("heading", { name: "Finding your next Short" })
      .waitFor();
    await capture("processing");
    await page
      .getByRole("button", { name: "Review best moments" })
      .waitFor({ timeout: 30000 });
    await page.getByRole("button", { name: "Review best moments" }).click();
    await page
      .getByRole("heading", { name: /We found \d+ potential Shorts?/ })
      .waitFor();
    await capture("suggestions");
    await page
      .getByRole("button", { name: "Edit transcript", exact: true })
      .click();
    const transcript = page.getByRole("textbox").first();
    await transcript.waitFor();
    await transcript.fill(
      (await transcript.inputValue()) + " A corrected word.",
    );
    await capture("transcript-edited");
    await page.getByRole("button", { name: "Save 1 edit" }).click();
    await page.getByRole("button", { name: "Saved", exact: true }).waitFor();
    await capture("transcript");
    await page
      .getByRole("button", { name: "Back to moments", exact: true })
      .click();
    await page.getByText("Refine your suggestions", { exact: true }).click();
    await page
      .getByRole("button", { name: "Regenerate suggestions", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Finding new moments…", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: "Regenerate suggestions", exact: true })
      .waitFor({ timeout: 30000 });
    await page
      .getByRole("button", { name: "Preview", exact: true })
      .first()
      .click();
    await page.getByRole("heading", { name: "Make it a Short" }).waitFor();
    const video = page.locator(".video-preview video");
    await video.evaluate(async (el) => {
      await el.play();
    });
    await page.waitForTimeout(500);
    assert.equal(
      await video.evaluate((el) => !el.paused && el.videoWidth > 0),
      true,
      "Source preview plays",
    );
    await video.evaluate((el) => el.pause());
    assert.equal(
      await page
        .getByRole("button", { name: "Render Short", exact: true })
        .evaluate(
          (el) => el.getBoundingClientRect().bottom <= window.innerHeight,
        ),
      true,
      "Desktop Render remains above the fold",
    );
    await page.getByText("Captions & branding", { exact: true }).click();
    assert.equal(
      await page
        .getByRole("button", {
          name: "Clean caption style — not available yet",
        })
        .isDisabled(),
      true,
    );
    await page.getByText("Captions & branding", { exact: true }).click();
    await capture("editor");
    await page.getByRole("button", { name: "720p", exact: true }).click();
    await page
      .getByRole("button", { name: "Render Short", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Your Short is ready" })
      .waitFor({ timeout: 60000 });
    await video.evaluate(async (el) => {
      await el.play();
    });
    await page.waitForTimeout(400);
    assert.equal(
      await video.evaluate(
        (el) => !el.paused && el.videoWidth === 720 && el.videoHeight === 1280,
      ),
      true,
      "Rendered 720p Short plays",
    );
    await video.evaluate((el) => el.pause());
    await page.getByText("Cover & subtitle files", { exact: true }).click();
    assert.equal(
      await page
        .getByRole("button", { name: "Download SRT", exact: true })
        .isDisabled(),
      true,
    );
    await page.getByText("Cover & subtitle files", { exact: true }).click();
    await capture("result");
    const downloading = page.waitForEvent("download");
    await page
      .getByRole("link", { name: "Download Video", exact: true })
      .click();
    const download = await downloading;
    assert.equal(await download.failure(), null);
    assert.match(download.suggestedFilename(), /\.mp4$/);
    await page.getByRole("tab", { name: "TikTok", exact: true }).click();
    await page
      .getByRole("tab", { name: "TikTok", exact: true })
      .press("ArrowRight");
    assert.equal(
      await page
        .getByRole("tab", { name: "Instagram", exact: true })
        .getAttribute("aria-selected"),
      "true",
    );
    await nav("Usage");
    await capture("usage");
    await nav("Settings");
    await page.getByRole("button", { name: "Dark", exact: true }).click();
    assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
    await capture("settings-dark");
    await nav("Dashboard");
    await capture("dashboard-dark");
    await page.setViewportSize({ width: 390, height: 844 });
    await capture("dashboard-mobile-dark");
    assert.equal(
      await page.locator("#app-sidebar").evaluate((el) => el.inert),
      true,
    );
    await nav("New Project");
    await capture("new-mobile-dark");
    await nav("Settings");
    await page.getByRole("button", { name: "Light", exact: true }).click();
    await nav("Projects");
    await capture("projects-mobile");
    await page
      .getByRole("button", {
        name: path.basename(fixture, ".mp4"),
        exact: true,
      })
      .first()
      .click();
    await page
      .getByRole("button", { name: "Preview", exact: true })
      .first()
      .click();
    assert.equal(
      await page
        .getByRole("button", { name: "Render Short", exact: true })
        .evaluate(
          (el) => el.getBoundingClientRect().bottom <= window.innerHeight,
        ),
      true,
      "Mobile Render remains reachable",
    );
    await capture("editor-mobile");
    await page
      .getByRole("button", { name: "Back to moments", exact: true })
      .click();
    await page
      .getByRole("button", { name: "View Short", exact: true })
      .first()
      .click();
    await capture("result-mobile");
    await page.setViewportSize({ width: 820, height: 1180 });
    await capture("result-tablet");
    await nav("Projects");
    await page
      .getByRole("button", {
        name: `Delete ${path.basename(fixture, ".mp4")}`,
        exact: true,
      })
      .first()
      .click();
    await page.getByRole("dialog").waitFor();
    await page
      .getByRole("button", { name: "Keep project", exact: true })
      .press("Shift+Tab");
    assert.equal(
      await page
        .getByRole("button", { name: "Delete project", exact: true })
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page
      .getByRole("button", { name: "Delete project", exact: true })
      .click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    // Presentation states use intercepted read-only responses, keeping backend data intact.
    await page.route("**/v1/projects", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      }),
    );
    await nav("Dashboard");
    await page.getByRole("heading", { name: "No Shorts yet" }).waitFor();
    await capture("empty-state");
    await page.unroute("**/v1/projects");
    await page.route("**/v1/projects", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "SERVER_ERROR", message: "Please try again." },
        }),
      }),
    );
    await nav("Projects");
    await page
      .getByText("We couldn't load your projects.", { exact: true })
      .waitFor();
    await capture("error-state");
    await page.unroute("**/v1/projects");
    await page.route("**/v1/projects", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    });
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await page.getByLabel("Loading", { exact: true }).waitFor();
    await capture("loading-state");
    await page.getByRole("heading", { name: "No Shorts yet" }).waitFor();
    await page.unroute("**/v1/projects");
    await nav("Settings");
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.getByLabel("Email address").waitFor();
    assert.equal(
      await page.evaluate(() =>
        Object.keys(sessionStorage).some((key) =>
          key.startsWith("shorts-session:"),
        ),
      ),
      false,
    );
    await page.reload({ waitUntil: "networkidle" });
    await page.getByLabel("Email address").waitFor();
    assert.deepEqual(errors, []);
    console.log(
      "PASS: login refresh/logout, desktop/mobile/tablet, light/dark, upload → analysis → transcript → regeneration → preview → 720p render → download, URL consent, project search/delete, keyboard tabs/dialog.",
    );
    console.log(`Screenshots: ${output}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
