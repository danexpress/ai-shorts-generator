/* Social publishing UI; all network/state operations remain in the page controller. */
(function (root) {
  "use strict";
  const h = (...args) => root.React.createElement(...args);
  const names = { youtube: "YouTube", tiktok: "TikTok", x: "X" };
  function SocialAccounts({ v }) {
    const s = v.social || {};
    return h(
      "div",
      { className: "narrow stack" },
      h(
        "header",
        null,
        h("h1", { id: "page-title", tabIndex: -1 }, "Social Accounts"),
        h(
          "p",
          { className: "muted" },
          "Connect the accounts where you want to share your Shorts.",
        ),
      ),
      h(
        "button",
        { className: "btn ghost", onClick: () => v.navigate("settings") },
        "Back to Settings",
      ),
      h(
        "button",
        {
          className: "btn",
          disabled: s.loading || s.busy,
          onClick: v.loadSocialAccounts,
        },
        "Refresh connections",
      ),
      s.error &&
        h(
          "div",
          { className: "error-box", role: "alert" },
          s.error,
          h(
            "button",
            { className: "btn", onClick: v.loadSocialAccounts },
            "Try again",
          ),
        ),
      s.loading && h("p", { role: "status" }, "Loading connections…"),
      (s.platforms || []).map((p) => {
        const a = (s.accounts || []).find((a) => a.platform === p.platform);
        return h(
          "section",
          { className: "panel pad stack", key: p.platform },
          h(
            "div",
            { className: "row between" },
            h("h2", null, names[p.platform]),
            a &&
              h(
                "span",
                { className: "badge" },
                a.status === "connected" ? "Connected" : "Reconnect required",
              ),
          ),
          a &&
            h(
              "div",
              { className: "row" },
              a.avatar_url &&
                h("img", {
                  src: a.avatar_url,
                  alt: "",
                  className: "avatar",
                  referrerPolicy: "no-referrer",
                }),
              h(
                "div",
                null,
                h("strong", null, a.account_name),
                a.username && h("p", { className: "small muted" }, a.username),
              ),
            ),
          p.notice && h("p", { className: "small muted" }, p.notice),
          h(
            "div",
            { className: "row" },
            (!a || a.status !== "connected") &&
              h(
                "button",
                {
                  className: "btn primary",
                  disabled: !p.enabled || s.busy,
                  onClick: () => v.connectSocial(p.platform),
                },
                `${a ? "Reconnect" : "Connect"} ${names[p.platform]}`,
              ),
            a &&
              h(
                "button",
                {
                  className: "btn",
                  disabled: s.busy,
                  onClick: () => v.disconnectSocial(p.platform),
                },
                "Disconnect",
              ),
          ),
        );
      }),
    );
  }
  function PublishingHistory({ v }) {
    const state = v.publishing || {};
    if (!state.history?.length) return null;
    const status = {
      queued: "Waiting",
      uploading: "Uploading video…",
      processing: "Processing…",
      publishing: "Creating post…",
      published: "Published",
      failed: "Publishing failed",
    };
    return h(
      "section",
      { className: "panel pad stack", "aria-label": "Publishing History" },
      h("h2", null, "Publishing History"),
      state.history.map((batch) =>
        h(
          "div",
          { key: batch.id, className: "stack" },
          h(
            "p",
            { className: "small muted" },
            new Date(batch.created_at).toLocaleString(),
          ),
          batch.jobs.map((job) =>
            h(
              "div",
              { key: job.id, className: "social-job" },
              h(
                "div",
                null,
                h("strong", null, names[job.platform]),
                job.account_name &&
                  h("p", { className: "small muted" }, job.account_name),
              ),
              h(
                "div",
                { className: "stack" },
                h(
                  "span",
                  {
                    className: "badge",
                    role:
                      job.status === "published" || job.status === "failed"
                        ? undefined
                        : "status",
                  },
                  (job.status === "published"
                    ? "✓ "
                    : job.status === "failed"
                      ? "× "
                      : "") + status[job.status],
                ),
                job.error_message_safe &&
                  h("p", { className: "small muted" }, job.error_message_safe),
                h(
                  "div",
                  { className: "row" },
                  job.platform_post_url &&
                    h(
                      "a",
                      {
                        className: "btn sm",
                        href: job.platform_post_url,
                        target: "_blank",
                        rel: "noopener noreferrer",
                      },
                      "View Post",
                    ),
                  job.status === "failed" &&
                    job.retryable &&
                    h(
                      "button",
                      {
                        className: "btn sm",
                        disabled: state.busy,
                        onClick: () => v.retrySocialJob(job),
                      },
                      "Retry " + names[job.platform],
                    ),
                  job.status === "failed" &&
                    [
                      "RECONNECT_REQUIRED",
                      "PERMISSION_REQUIRED",
                      "ACCOUNT_DISCONNECTED",
                    ].includes(job.error_code) &&
                    h(
                      "button",
                      {
                        className: "btn sm",
                        onClick: () => v.connectSocial(job.platform),
                      },
                      "Reconnect " + names[job.platform],
                    ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
  function field(v, p, key, label, multiline = false, limit = null) {
    const c = v.publishing.copy[p] || {};
    const value = key === "hashtags" ? (c[key] || []).join(" ") : c[key] || "";
    const labelId = `social-${p}-${key}-label`,
      helpId = `social-${p}-${key}-help`;
    return h(
      "label",
      { className: "field", key: p + key },
      h("span", { className: "field-label", id: labelId }, label),
      h(multiline ? "textarea" : "input", {
        "aria-labelledby": labelId,
        "aria-describedby": limit || key === "hashtags" ? helpId : undefined,
        value,
        rows: multiline ? 4 : undefined,
        maxLength: limit || undefined,
        disabled: v.publishing.busy,
        onChange: (e) =>
          v.editSocialCopy(
            p,
            key,
            key === "hashtags"
              ? e.target.value.split(/[\s,]+/).filter(Boolean)
              : e.target.value,
          ),
        onBlur: () => v.saveSocialDraft(p),
      }),
      limit &&
        h(
          "span",
          { className: "small muted", id: helpId },
          `${value.length} / ${limit}`,
        ),
      key === "hashtags" &&
        h(
          "span",
          { className: "small muted", id: helpId },
          "Up to 5 relevant hashtags, each starting with #.",
        ),
    );
  }
  function toggle(v, p, key, label, disabled = false) {
    return h(
      "label",
      { className: "check-row", key: p + key },
      h("input", {
        type: "checkbox",
        checked: !!v.publishing.settings[p]?.[key],
        disabled: disabled || v.publishing.busy,
        onChange: (e) => v.editPublishSetting(p, key, e.target.checked),
      }),
      h("span", null, label),
    );
  }
  function Publish({ v }) {
    const state = v.publishing || {},
      social = v.social || {},
      busy = state.busy || state.loading;
    const selected = Object.keys(state.selected || {}).filter(
      (p) => state.selected[p],
    );
    const existing = (state.history || []).flatMap((b) => b.jobs);
    const remaining = (social.platforms || []).filter(
      (p) => !existing.some((j) => j.platform === p.platform),
    );
    const active = existing.some(
      (j) => !["published", "failed"].includes(j.status),
    );
    const allDone =
      existing.length && existing.every((j) => j.status === "published");
    const tiktok = state.settings?.tiktok || {},
      creator = state.creator;
    const valid =
      selected.length &&
      selected.every((p) => {
        const c = state.copy?.[p] || {};
        if (p === "youtube") return c.title?.trim() && c.description?.trim();
        if (p === "tiktok")
          return (
            c.caption?.trim() &&
            creator &&
            tiktok.privacy &&
            tiktok.consent &&
            !(tiktok.brand_content && tiktok.privacy === "SELF_ONLY")
          );
        return c.text?.trim();
      });
    return h(
      root.React.Fragment,
      null,
      h(
        "button",
        { className: "btn ghost mb-5", onClick: () => v.navigate("result") },
        "Back to your Short",
      ),
      h(root.ShortsUI.PageHeader, {
        title: active
          ? "Publishing your Short"
          : allDone
            ? "Published Successfully"
            : existing.length
              ? "Publishing Results"
              : "Ready to Publish",
        description: active
          ? "Each platform is progressing independently. You can leave this screen; the jobs are saved."
          : allDone
            ? "Your Short is published to your selected accounts."
            : existing.length
              ? "Your results are saved. Retry only the accounts that need attention."
              : "Review the video and copy, then choose where to share it.",
      }),
      h(
        "div",
        { className: "editor" },
        h(
          "div",
          { className: "preview-stage" },
          h(root.ShortsUI.VideoPreview, {
            url: v.result?.url,
            label: "Short to publish",
          }),
        ),
        h(
          "div",
          { className: "stack" },
          state.error &&
            h(root.ShortsUI.ErrorState, {
              title: "Publishing needs your attention",
              message: state.error,
              retry: v.refreshPublish,
            }),
          h(PublishingHistory, { v }),
          remaining.length > 0 &&
            social.copy_available &&
            state.sourceTranscript &&
            h(
              "section",
              { className: "panel pad stack" },
              h("h2", null, "Review the transcript"),
              h(
                "p",
                { className: "small muted" },
                "Transcription currently uses demo data. Confirm that the saved transcript matches your video before generating copy. You can also write your own copy below.",
              ),
              h(
                "details",
                null,
                h("summary", null, "Saved clip transcript"),
                h("p", { className: "small" }, state.sourceTranscript),
              ),
              h(
                "label",
                { className: "check-row" },
                h("input", {
                  type: "checkbox",
                  checked: !!state.transcriptConfirmed,
                  disabled: busy,
                  onChange: (e) => v.confirmSocialTranscript(e.target.checked),
                }),
                "I reviewed the transcript and it matches this Short.",
              ),
            ),
          state.loading
            ? h(
                "div",
                {
                  className: "panel pad skeleton",
                  "aria-label": "Loading publishing settings",
                },
                h("p", null, "Loading publishing settings…"),
              )
            : remaining.length > 0 &&
                h(
                  root.React.Fragment,
                  null,
                  h(
                    "section",
                    { className: "panel pad stack" },
                    h(
                      "div",
                      { className: "row between" },
                      h("h2", null, "Publish to"),
                      h(
                        "button",
                        {
                          className: "btn sm",
                          onClick: () => v.navigate("socialAccounts"),
                        },
                        "Manage accounts",
                      ),
                    ),
                    remaining.map((p) => {
                      const account = (social.accounts || []).find(
                          (a) => a.platform === p.platform,
                        ),
                        already = existing.some(
                          (j) => j.platform === p.platform,
                        ),
                        disabled =
                          busy ||
                          !p.enabled ||
                          account?.status !== "connected" ||
                          already;
                      return h(
                        "div",
                        { className: "social-target", key: p.platform },
                        h(
                          "label",
                          { className: "check-row" },
                          h("input", {
                            type: "checkbox",
                            disabled,
                            checked: !!state.selected[p.platform] && !already,
                            onChange: (e) =>
                              v.selectPublishPlatform(
                                p.platform,
                                e.target.checked,
                              ),
                          }),
                          h(
                            "span",
                            null,
                            h("strong", null, names[p.platform]),
                            h(
                              "span",
                              { className: "small muted block" },
                              already
                                ? "See publishing status below"
                                : account
                                  ? account.account_name
                                  : "No account connected",
                            ),
                          ),
                        ),
                        (!account || account.status !== "connected") &&
                          h(
                            "button",
                            {
                              className: "btn sm",
                              disabled: !p.enabled || busy || social.busy,
                              onClick: () => v.connectSocial(p.platform),
                            },
                            (account ? "Reconnect " : "Connect ") +
                              names[p.platform],
                          ),
                        p.notice &&
                          h(
                            "p",
                            { className: "small muted social-target-note" },
                            p.notice,
                          ),
                      );
                    }),
                  ),
                  !social.copy_available &&
                    h(
                      "p",
                      { className: "small muted" },
                      "AI copy generation is not enabled yet. You can write platform-specific copy below.",
                    ),
                  ["youtube", "tiktok", "x"]
                    .filter(
                      (p) =>
                        state.selected[p] &&
                        !existing.some((j) => j.platform === p),
                    )
                    .map((p) =>
                      h(
                        "section",
                        { className: "panel pad stack", key: p },
                        h(
                          "div",
                          { className: "row between" },
                          h("h2", null, names[p]),
                          h(
                            "button",
                            {
                              className: "btn sm",
                              disabled:
                                busy ||
                                !social.copy_available ||
                                !state.transcriptConfirmed,
                              onClick: () => v.generateSocialCopy(p),
                            },
                            "Regenerate " + names[p],
                          ),
                        ),
                        p === "youtube"
                          ? h(
                              root.React.Fragment,
                              null,
                              field(v, p, "title", "YouTube title", false, 100),
                              field(
                                v,
                                p,
                                "description",
                                "YouTube description",
                                true,
                                4800,
                              ),
                              field(v, p, "hashtags", "YouTube hashtags"),
                              h(
                                "label",
                                { className: "field" },
                                h(
                                  "span",
                                  { className: "field-label" },
                                  "YouTube visibility",
                                ),
                                h(
                                  "select",
                                  {
                                    value: state.settings.youtube.visibility,
                                    "aria-label": "YouTube visibility",
                                    disabled: busy,
                                    onChange: (e) =>
                                      v.editPublishSetting(
                                        p,
                                        "visibility",
                                        e.target.value,
                                      ),
                                  },
                                  ["private", "unlisted", "public"].map((o) =>
                                    h(
                                      "option",
                                      {
                                        key: o,
                                        value: o,
                                        disabled:
                                          o !== "private" &&
                                          social.platforms.find(
                                            (p) => p.platform === "youtube",
                                          )?.private_only,
                                      },
                                      o[0].toUpperCase() + o.slice(1),
                                    ),
                                  ),
                                ),
                              ),
                              toggle(
                                v,
                                p,
                                "made_for_kids",
                                "This video is made for kids",
                              ),
                              toggle(
                                v,
                                p,
                                "contains_synthetic_media",
                                "Contains realistic altered or synthetic content",
                              ),
                              toggle(
                                v,
                                p,
                                "use_thumbnail",
                                "Use the first video frame as a YouTube cover",
                              ),
                            )
                          : p === "tiktok"
                            ? h(
                                root.React.Fragment,
                                null,
                                field(
                                  v,
                                  p,
                                  "caption",
                                  "TikTok caption",
                                  true,
                                  2200,
                                ),
                                field(v, p, "hashtags", "TikTok hashtags"),
                                creator &&
                                  h(
                                    "p",
                                    { className: "small muted" },
                                    "Posting as " +
                                      creator.account_name +
                                      (creator.username
                                        ? " · @" + creator.username
                                        : ""),
                                  ),
                                state.creatorError &&
                                  h(
                                    "p",
                                    { role: "alert", className: "small" },
                                    state.creatorError,
                                  ),
                                h(
                                  "label",
                                  { className: "field" },
                                  h(
                                    "span",
                                    { className: "field-label" },
                                    "TikTok privacy",
                                  ),
                                  h(
                                    "select",
                                    {
                                      value: tiktok.privacy,
                                      "aria-label": "TikTok privacy",
                                      disabled: busy,
                                      disabled: !creator,
                                      onChange: (e) =>
                                        v.editPublishSetting(
                                          p,
                                          "privacy",
                                          e.target.value,
                                        ),
                                    },
                                    h(
                                      "option",
                                      { value: "" },
                                      "Choose privacy",
                                    ),
                                    (creator?.privacy_options || []).map((o) =>
                                      h(
                                        "option",
                                        { key: o, value: o },
                                        {
                                          PUBLIC_TO_EVERYONE: "Everyone",
                                          MUTUAL_FOLLOW_FRIENDS: "Friends",
                                          FOLLOWER_OF_CREATOR: "Followers",
                                          SELF_ONLY: "Only me",
                                        }[o] || o,
                                      ),
                                    ),
                                  ),
                                ),
                                toggle(
                                  v,
                                  p,
                                  "allow_comment",
                                  "Allow comments",
                                  !creator || creator.comment_disabled,
                                ),
                                toggle(
                                  v,
                                  p,
                                  "allow_duet",
                                  "Allow Duet",
                                  !creator || creator.duet_disabled,
                                ),
                                toggle(
                                  v,
                                  p,
                                  "allow_stitch",
                                  "Allow Stitch",
                                  !creator || creator.stitch_disabled,
                                ),
                                h(
                                  "details",
                                  null,
                                  h("summary", null, "Content disclosures"),
                                  h(
                                    "div",
                                    { className: "stack mt-3" },
                                    toggle(
                                      v,
                                      p,
                                      "brand_organic",
                                      "Promotes my own brand",
                                    ),
                                    toggle(
                                      v,
                                      p,
                                      "brand_content",
                                      "Paid partnership / third-party brand",
                                      tiktok.privacy === "SELF_ONLY",
                                    ),
                                    toggle(
                                      v,
                                      p,
                                      "is_aigc",
                                      "AI-generated or significantly AI-altered content",
                                    ),
                                    tiktok.brand_organic &&
                                      h(
                                        "p",
                                        { className: "small muted" },
                                        "TikTok will label this as Promotional content.",
                                      ),
                                    tiktok.brand_content &&
                                      h(
                                        "p",
                                        { className: "small muted" },
                                        "TikTok will label this as Paid partnership. This cannot be private.",
                                      ),
                                  ),
                                ),
                                h(
                                  "label",
                                  { className: "check-row" },
                                  h("input", {
                                    type: "checkbox",
                                    checked: !!tiktok.consent,
                                    onChange: (e) =>
                                      v.editPublishSetting(
                                        p,
                                        "consent",
                                        e.target.checked,
                                      ),
                                  }),
                                  h(
                                    "span",
                                    null,
                                    "By posting, I agree to TikTok’s ",
                                    h(
                                      "a",
                                      {
                                        href: "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en",
                                        target: "_blank",
                                        rel: "noopener noreferrer",
                                      },
                                      "Music Usage Confirmation",
                                    ),
                                    tiktok.brand_content &&
                                      h(
                                        root.React.Fragment,
                                        null,
                                        " and ",
                                        h(
                                          "a",
                                          {
                                            href: "https://www.tiktok.com/legal/page/global/bc-policy/en",
                                            target: "_blank",
                                            rel: "noopener noreferrer",
                                          },
                                          "Branded Content Policy",
                                        ),
                                      ),
                                  ),
                                ),
                              )
                            : field(v, p, "text", "X post", true, 280),
                      ),
                    ),
                  h(
                    "div",
                    { className: "publish-actions" },
                    h(
                      "button",
                      {
                        className: "btn",
                        disabled:
                          busy ||
                          !social.copy_available ||
                          !state.transcriptConfirmed ||
                          active,
                        onClick: () => v.generateSocialCopy(),
                      },
                      state.busy ? "Working…" : "Regenerate All Copy",
                    ),
                    h(
                      "button",
                      {
                        className: "btn primary",
                        disabled:
                          busy ||
                          !valid ||
                          active ||
                          selected.some((p) =>
                            existing.some((j) => j.platform === p),
                          ),
                        onClick: v.publishEverywhere,
                      },
                      state.busy ? "Working…" : "Publish Everywhere",
                    ),
                  ),
                  h(
                    "p",
                    { className: "small muted" },
                    "Publishing starts only when you click Publish Everywhere. Review all selected copy before posting.",
                  ),
                ),
        ),
      ),
    );
  }
  root.ShortsSocialUI = { SocialAccounts, Publish, PublishingHistory };
})(window);
