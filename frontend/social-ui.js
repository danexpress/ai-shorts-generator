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
  root.ShortsSocialUI = { SocialAccounts };
})(window);
