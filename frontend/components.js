/* Presentation-only React components. API orchestration stays in the page controller. */
(function (root) {
  "use strict";
  const h = (...args) => root.React.createElement(...args);
  const paths = {
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    plus: "M12 5v14 M5 12h14",
    folder: "M3 6h7l2 2h9v12H3z",
    usage: "M4 20V10 M10 20V4 M16 20v-8 M22 20H2",
    settings:
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
    video: "M3 5h13v14H3z M16 9l5-3v12l-5-3",
    play: "m9 5 11 7-11 7z",
    arrow: "M4 12h16 M14 6l6 6-6 6",
    back: "M20 12H4 M10 6l-6 6 6 6",
    upload: "M12 16V3 M7 8l5-5 5 5 M4 16v5h16v-5",
    download: "M12 3v13 M7 11l5 5 5-5 M4 17v4h16v-4",
    check: "m5 12 4 4L19 6",
    close: "m6 6 12 12 M18 6 6 18",
    menu: "M4 6h16 M4 12h16 M4 18h16",
    chevron: "m9 5 7 7-7 7",
    down: "m6 9 6 6 6-6",
    clock: "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
    alert: "M12 8v5 M12 17h.01 M12 3 2 21h20z",
    spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
    copy: "M9 9h12v12H9z M15 9V3H3v12h6",
    trash: "M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7",
    logout: "M9 3H3v18h6 M9 12h12 M16 7l5 5-5 5",
    link: "m9 15 6-6 M7 14l-2 2a3 3 0 0 0 4 4l4-4 M11 8l4-4a3 3 0 0 1 4 4l-2 2",
  };
  function Icon({ name, ...props }) {
    return h(
      "svg",
      {
        viewBox: "0 0 24 24",
        className: "icon",
        "aria-hidden": true,
        ...props,
      },
      h("path", { d: paths[name] || paths.video }),
    );
  }
  function Button({
    children,
    icon,
    primary,
    ghost,
    small,
    className = "",
    ...props
  }) {
    return h(
      "button",
      {
        type: "button",
        className: `btn ${primary ? "primary" : ""} ${ghost ? "ghost" : ""} ${small ? "sm" : ""} ${className}`,
        ...props,
      },
      icon && h(Icon, { name: icon }),
      children,
    );
  }
  function Brand() {
    return h(
      "div",
      { className: "brand" },
      h("span", { className: "brand-mark" }, h(Icon, { name: "play" })),
      "Shorts",
      h("span", { className: "muted brand-suffix" }, "Studio"),
    );
  }
  function PageHeader({ title, description, children }) {
    return h(
      "header",
      { className: "page-header" },
      h(
        "div",
        null,
        h("h1", { tabIndex: -1, id: "page-title" }, title),
        description && h("p", null, description),
      ),
      children && h("div", { className: "header-actions" }, children),
    );
  }
  function StatusBadge({ status, label }) {
    const kind =
      status === "ready"
        ? "ready"
        : ["failed", "expired", "canceled"].includes(status)
          ? "failed"
          : ["uploading", "transcribing", "analyzing", "processing"].includes(
                status,
              )
            ? "processing"
            : "";
    return h(
      "span",
      { className: `badge ${kind}` },
      h("span", { className: "badge-dot" }),
      label || status,
    );
  }
  function UsageIndicator({ used, limit, compact = false }) {
    return h(
      "div",
      { className: compact ? "usage-top small muted" : "usage-mini" },
      h(
        "div",
        { className: "row between small" },
        h(
          "span",
          null,
          h("strong", null, used),
          limit === null ? " min used" : ` / ${limit} min used`,
        ),
        !compact &&
          limit === null &&
          h("span", { className: "badge" }, "Unlimited"),
      ),
      !compact &&
        limit !== null &&
        h(
          "div",
          {
            className: "meter",
            role: "meter",
            "aria-label": "Monthly processing minutes",
            "aria-valuenow": used,
            "aria-valuemax": Math.max(limit, used),
            "aria-valuemin": 0,
          },
          h("div", {
            className: "meter-fill",
            style: { width: `${Math.min(100, (used / (limit || 1)) * 100)}%` },
          }),
        ),
      !compact &&
        h("p", { className: "small muted mt-2" }, "Monthly processing"),
    );
  }
  function AppSidebar({ v, open, close }) {
    const [mobile, setMobile] = root.React.useState(
      () => window.matchMedia("(max-width: 900px)").matches,
    );
    const sidebar = root.React.useRef(null);
    root.React.useEffect(() => {
      const query = window.matchMedia("(max-width: 900px)");
      const update = () => setMobile(query.matches);
      query.addEventListener("change", update);
      return () => query.removeEventListener("change", update);
    }, []);
    root.React.useEffect(() => {
      if (!open || !mobile) return;
      const previous = document.activeElement;
      sidebar.current?.querySelector("button")?.focus();
      const overflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = overflow;
        if (sidebar.current?.contains(document.activeElement))
          previous?.focus();
      };
    }, [open, mobile]);
    const active = [
      "suggestions",
      "editor",
      "result",
      "transcript",
      "processing",
    ].includes(v.route)
      ? "projects"
      : v.route;
    const items = [
      ["dashboard", "Dashboard", "grid"],
      ["new", "New Project", "plus"],
      ["projects", "Projects", "folder"],
      ["usage", "Usage", "usage"],
      ["settings", "Settings", "settings"],
    ];
    return h(
      root.React.Fragment,
      null,
      open &&
        h("button", {
          className: "mobile-backdrop",
          onClick: close,
          "aria-label": "Close navigation",
          tabIndex: -1,
        }),
      h(
        "aside",
        {
          className: "sidebar",
          ref: sidebar,
          inert: mobile && !open ? "" : undefined,
          onKeyDown: (e) => {
            if (!mobile || !open || e.key !== "Tab") return;
            const items = [
              ...sidebar.current.querySelectorAll("button, summary, a"),
            ].filter((el) => el.getClientRects().length);
            const first = items[0],
              last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first.focus();
            }
          },
          id: "app-sidebar",
          "aria-label": "Workspace navigation",
        },
        h(Brand),
        h(Button, {
          className: "mobile-menu icon-btn",
          ghost: true,
          icon: "close",
          onClick: close,
          "aria-label": "Close navigation",
        }),
        h(
          "nav",
          { className: "nav", "aria-label": "Main" },
          items.map(([id, label, icon]) =>
            h(
              "button",
              {
                key: id,
                className: `nav-btn ${id === "new" ? "new" : ""}`,
                "aria-current": active === id ? "page" : undefined,
                onClick: () => {
                  v.navigate(id);
                  close();
                },
              },
              h(Icon, { name: icon }),
              label,
            ),
          ),
        ),
        h(
          "div",
          { className: "sidebar-bottom" },
          h(UsageIndicator, { used: v.usageUsed, limit: v.usageLimit }),
          h(
            "details",
            { className: "account" },
            h(
              "summary",
              { "aria-label": `Account menu for ${v.userName}` },
              h("span", { className: "avatar" }, v.initials),
              h(
                "div",
                { className: "grow" },
                h("div", { className: "small truncate" }, v.userName),
                h(
                  "div",
                  { className: "muted truncate account-email" },
                  v.email,
                ),
              ),
              h(Icon, { name: "down" }),
            ),
            h(
              "div",
              { className: "account-menu" },
              h(
                Button,
                {
                  small: true,
                  onClick: () => {
                    v.navigate("settings");
                    close();
                  },
                },
                "Account",
              ),
              h(
                Button,
                { small: true, icon: "logout", onClick: v.signOut },
                "Sign out",
              ),
            ),
          ),
        ),
      ),
    );
  }
  function EmptyState({
    primary = true,
    title = "No Shorts yet",
    description = "Upload your first long-form video and let AI find the best moments.",
    action,
    label = "Create your first project",
    icon = "video",
  }) {
    return h(
      "div",
      { className: "empty" },
      h("span", { className: "empty-symbol" }, h(Icon, { name: icon })),
      h("h2", null, title),
      h("p", null, description),
      action && h(Button, { primary, icon: "plus", onClick: action }, label),
    );
  }
  function ErrorState({
    title = "We couldn’t load this screen.",
    message = "Check your connection, then try again.",
    retry,
  }) {
    return h(
      "div",
      { className: "notice error", role: "alert" },
      h(Icon, { name: "alert" }),
      h(
        "div",
        null,
        h("strong", null, title),
        message && h("p", null, message),
        retry && h(Button, { small: true, onClick: retry }, "Try again"),
      ),
    );
  }
  function Skeleton({ cards = false }) {
    return h(
      "div",
      {
        className: cards ? "clip-grid" : "panel",
        "aria-busy": true,
        "aria-label": "Loading",
      },
      [1, 2, 3].map((n) =>
        cards
          ? h(
              "div",
              {
                key: n,
                className: "panel clip-card skeleton-card",
                "aria-hidden": true,
              },
              h("div", { className: "skeleton clip-visual" }),
              h(
                "div",
                { className: "clip-body" },
                h("div", { className: "skeleton skeleton-heading" }),
                h("div", { className: "skeleton skeleton-line short-line" }),
                h(
                  "div",
                  { className: "skeleton-copy" },
                  h("div", { className: "skeleton skeleton-line" }),
                  h("div", { className: "skeleton skeleton-line" }),
                ),
                h(
                  "div",
                  { className: "scores" },
                  [1, 2, 3, 4].map((i) =>
                    h("div", { key: i, className: "skeleton skeleton-score" }),
                  ),
                ),
                h(
                  "div",
                  { className: "clip-actions" },
                  h("div", { className: "skeleton skeleton-button" }),
                  h("div", { className: "skeleton skeleton-button" }),
                ),
              ),
            )
          : h(
              "div",
              { key: n, className: "skeleton-row", "aria-hidden": true },
              h("div", { className: "skeleton skeleton-thumb" }),
              h(
                "div",
                { className: "skeleton-copy" },
                h("div", { className: "skeleton skeleton-line" }),
                h("div", { className: "skeleton skeleton-line short-line" }),
              ),
            ),
      ),
    );
  }
  function SourceThumb({ url }) {
    const [failed, setFailed] = root.React.useState(false);
    root.React.useEffect(() => setFailed(false), [url]);
    return h(
      "div",
      { className: "thumb" },
      url && !failed
        ? h("video", {
            src: url,
            onError: () => setFailed(true),
            muted: true,
            playsInline: true,
            preload: "metadata",
            "aria-hidden": true,
          })
        : h(Icon, { name: "video" }),
    );
  }
  function ProjectTable({ projects, onDelete, compact }) {
    return h(
      "div",
      { className: "panel table-scroll" },
      h(
        "table",
        { className: "project-table" },
        h(
          "caption",
          { className: "sr-only" },
          compact ? "Recent projects" : "All projects",
        ),
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { scope: "col" }, "Video / Project"),
            h("th", { scope: "col" }, "Status"),
            h("th", { scope: "col", className: "hide-mobile" }, "Clips"),
            h("th", { scope: "col", className: "hide-mobile" }, "Created"),
            !compact &&
              h(
                "th",
                { scope: "col", className: "hide-tablet" },
                "Source expires",
              ),
            h(
              "th",
              { scope: "col" },
              h("span", { className: "sr-only" }, "Actions"),
            ),
          ),
        ),
        h(
          "tbody",
          null,
          projects.map((p) =>
            h(
              "tr",
              { key: p.id },
              h(
                "td",
                null,
                h(
                  "div",
                  { className: "row" },
                  h(SourceThumb, { url: p.thumbnailUrl }),
                  h(
                    "div",
                    { className: "grow" },
                    h(
                      "button",
                      { className: "name-btn", onClick: p.open, title: p.name },
                      p.name,
                    ),
                    h(
                      "div",
                      { className: "small muted project-type truncate" },
                      p.typeLabel,
                      " · ",
                      p.durationLabel,
                    ),
                  ),
                ),
              ),
              h(
                "td",
                null,
                h(StatusBadge, { status: p.status, label: p.statusLabel }),
              ),
              h("td", { className: "hide-mobile" }, p.clipCount),
              h("td", { className: "hide-mobile muted" }, p.createdLabel),
              !compact &&
                h(
                  "td",
                  { className: "hide-tablet muted" },
                  p.sourceExpiryLabel,
                ),
              h(
                "td",
                null,
                h(
                  "div",
                  { className: "row tight" },
                  h(Button, {
                    ghost: true,
                    small: true,
                    icon: "chevron",
                    onClick: p.open,
                    "aria-label": `Open ${p.name}`,
                  }),
                  !compact &&
                    h(Button, {
                      ghost: true,
                      small: true,
                      icon: "trash",
                      onClick: () => onDelete(p),
                      "aria-label": `Delete ${p.name}`,
                    }),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
  function Dashboard({ v }) {
    return h(
      root.React.Fragment,
      null,
      h(
        PageHeader,
        {
          title: "Dashboard",
          description: "Create, review, and download your latest Shorts.",
        },
        h(Button, { icon: "plus", onClick: v.goNew }, "New Project"),
      ),
      h(
        "section",
        { className: "hero", "aria-label": "Create Shorts" },
        h(
          "div",
          null,
          h("h2", null, "Create Shorts from a video"),
          h(
            "p",
            null,
            "Find the moments worth sharing. Turn your interviews, podcasts, and ideas into vertical videos.",
          ),
          h(
            Button,
            { primary: true, icon: "arrow", onClick: v.goNew },
            "Create a new project",
          ),
        ),
        h(
          "div",
          { className: "hero-art", "aria-hidden": true },
          h("div", { className: "source-art" }, h(Icon, { name: "video" })),
          h(Icon, { name: "arrow" }),
          h(
            "div",
            { className: "short-art" },
            h(Icon, { name: "play" }),
            h("span"),
          ),
        ),
      ),
      h(
        "div",
        { className: "workflow", "aria-label": "Workflow" },
        ["Upload a video", "Review the best moments", "Render & publish"].map(
          (text, i) =>
            h("span", { key: text }, h("b", null, `0${i + 1}`), text),
        ),
      ),
      v.hasActiveJob &&
        h(
          "div",
          { className: "notice row between mt-5" },
          h(
            "div",
            null,
            h("strong", null, v.activeJobStage),
            " ",
            v.activeJobName,
          ),
          h(Button, { small: true, onClick: v.openActive }, "View progress"),
        ),
      h(
        "div",
        { className: "section-head" },
        h("h2", null, "Recent projects"),
        h(
          Button,
          {
            ghost: true,
            small: true,
            onClick: () => v.navigate("projects"),
            icon: "arrow",
          },
          "View all projects",
        ),
      ),
      v.dashboardError
        ? h(ErrorState, {
            title: "We couldn't load your projects.",
            message: "Check your connection and try again.",
            retry: v.reloadProjects,
          })
        : v.dashboardLoading
          ? h(Skeleton)
          : v.noProjects
            ? h(
                "div",
                { className: "panel" },
                h(EmptyState, { action: v.goNew, primary: false }),
              )
            : h(ProjectTable, {
                projects: v.projects.slice(0, 5),
                compact: true,
              }),
    );
  }
  function Projects({ v, onDelete }) {
    const [query, setQuery] = root.React.useState("");
    const [filter, setFilter] = root.React.useState("all");
    const projects = v.projects.filter(
      (p) =>
        p.name.toLowerCase().includes(query.toLowerCase()) &&
        (filter === "all" ||
          (filter === "processing"
            ? ["uploading", "transcribing", "analyzing"].includes(p.status)
            : p.status === filter)),
    );
    return h(
      root.React.Fragment,
      null,
      h(
        PageHeader,
        {
          title: "Projects",
          description:
            "Every source video, best moment, and finished Short in one place.",
        },
        h(
          Button,
          { primary: true, icon: "plus", onClick: v.goNew },
          "New Project",
        ),
      ),
      h(
        "div",
        { className: "toolbar" },
        h("input", {
          type: "search",
          className: "search",
          placeholder: "Search projects…",
          "aria-label": "Search projects",
          value: query,
          onChange: (e) => setQuery(e.target.value),
        }),
        h(
          "select",
          {
            value: filter,
            onChange: (e) => setFilter(e.target.value),
            "aria-label": "Filter projects by status",
            className: "status-filter",
          },
          ["all", "ready", "processing", "failed", "expired"].map((f) =>
            h(
              "option",
              { key: f, value: f },
              f === "all" ? "All statuses" : f[0].toUpperCase() + f.slice(1),
            ),
          ),
        ),
      ),
      v.dashboardError
        ? h(ErrorState, {
            title: "We couldn't load your projects.",
            retry: v.reloadProjects,
          })
        : v.dashboardLoading
          ? h(Skeleton)
          : projects.length
            ? h(ProjectTable, { projects, onDelete })
            : h(
                "div",
                { className: "panel" },
                h(EmptyState, {
                  title: v.noProjects ? undefined : "No matching projects",
                  description: v.noProjects
                    ? undefined
                    : "Try a different name or status.",
                  action: v.noProjects ? v.goNew : undefined,
                }),
              ),
    );
  }
  function Field({ id, label, optional, hint, children }) {
    return h(
      "div",
      { className: "field" },
      h(
        "label",
        { htmlFor: id },
        label,
        optional && h("span", { className: "optional" }, "Optional"),
      ),
      children,
      hint && h("p", { className: "small muted", id: `${id}-hint` }, hint),
    );
  }
  function Segments({ items, label }) {
    return h(
      "div",
      { className: "segmented", role: "group", "aria-label": label },
      items.map((item) =>
        h(
          "button",
          {
            key: item.id || item.label,
            type: "button",
            className: "segment",
            "aria-pressed": item.pressed || item.checked || "false",
            onClick: item.pick,
            disabled: item.disabled,
          },
          item.label,
        ),
      ),
    );
  }
  function UploadDropzone({ onFile, probing }) {
    const [dragging, setDragging] = root.React.useState(false);
    return h(
      "label",
      {
        className: `dropzone ${dragging ? "dragging" : ""}`,
        onDragOver: (e) => {
          e.preventDefault();
          setDragging(true);
        },
        onDragLeave: () => setDragging(false),
        onDrop: (e) => {
          e.preventDefault();
          setDragging(false);
          if (!probing) onFile({ target: { files: e.dataTransfer.files } });
        },
      },
      h("input", {
        type: "file",
        accept: ".mp4,.mov,.webm",
        onChange: (e) => {
          onFile(e);
          e.target.value = "";
        },
        disabled: probing,
        "aria-label": "Choose a video to upload",
      }),
      h("span", { className: "empty-symbol" }, h(Icon, { name: "upload" })),
      h(
        "strong",
        null,
        probing ? "Reading your video…" : "Drop your video here",
      ),
      h(
        "span",
        { className: "muted small" },
        probing
          ? "Checking duration and format"
          : "or click to browse your files",
      ),
      h(
        "span",
        { className: "small muted" },
        "MP4, MOV, WebM · 30 seconds–3 hours · Up to 4 GB",
      ),
    );
  }
  function SourceSummary({ title, url, detail, children }) {
    return h(
      "div",
      { className: "source-summary" },
      h(SourceThumb, { url }),
      h(
        "div",
        { className: "grow" },
        h("h3", null, title),
        detail && h("div", { className: "small muted" }, detail),
      ),
      children,
    );
  }
  function NewProject({ v }) {
    const reveal = v.hasSource || (v.isUrlTab && v.npUrl.trim());
    return h(
      "div",
      { className: "narrow" },
      h(PageHeader, {
        title: "Turn a long video into publish-ready Shorts",
        description:
          "Start with a video. We’ll help you find the moments that matter.",
      }),
      h(
        "div",
        { className: "panel pad stack" },
        h(
          "div",
          { className: "step-label" },
          h("span", { className: "step-number" }, "1"),
          "Add your source video",
        ),
        h(Segments, { items: v.tabs, label: "Video source" }),
        v.isUploadTab
          ? v.hasSource
            ? h(
                SourceSummary,
                {
                  title: v.srcName,
                  url: v.pendingPreviewUrl,
                  detail: v.srcSpecs
                    .slice(0, 3)
                    .map((x) => x.value)
                    .join(" · "),
                },
                h(Button, {
                  ghost: true,
                  small: true,
                  icon: "close",
                  onClick: v.clearSource,
                  "aria-label": "Remove selected video",
                }),
              )
            : h(UploadDropzone, { onFile: v.onFile, probing: v.probing })
          : h(
              "div",
              { className: "stack" },
              h(
                Field,
                {
                  id: "youtube-url",
                  label: "YouTube video URL",
                  hint: "Public videos, 30 seconds–3 hours. The video is downloaded before analysis.",
                },
                h("input", {
                  id: "youtube-url",
                  type: "url",
                  value: v.npUrl,
                  onChange: v.onUrl,
                  placeholder: "https://www.youtube.com/watch?v=…",
                  "aria-describedby": "youtube-url-hint",
                }),
              ),
              h(
                "label",
                { className: "check" },
                h("input", {
                  type: "checkbox",
                  checked: v.npRights,
                  onChange: v.onRights,
                }),
                "I own this video or have permission to download and repurpose it.",
              ),
            ),
        v.npError &&
          h(ErrorState, {
            title: "We couldn't use this video.",
            message: v.npError,
          }),
      ),
      reveal &&
        h(
          "section",
          {
            className: "panel pad stack mt-5",
            "aria-label": "AI clip preferences",
          },
          h(
            "div",
            { className: "step-label" },
            h("span", { className: "step-number" }, "2"),
            "Give the AI a little direction",
          ),
          h(
            "div",
            { className: "field" },
            h("span", { className: "field-label" }, "What’s your clip goal?"),
            h(Segments, { items: v.goals, label: "AI clip goal" }),
          ),
          h(
            Field,
            {
              id: "instruction",
              label: "What should the AI look for?",
              optional: true,
            },
            h("textarea", {
              id: "instruction",
              value: v.npInstruction,
              maxLength: 200,
              onChange: v.onInstruction,
              placeholder:
                "Find the strongest moments where we discuss career advice.",
            }),
          ),
          h(
            "details",
            null,
            h("summary", null, "Topics to avoid · optional"),
            h(
              Field,
              {
                id: "avoid",
                label: "Topics to avoid",
                hint: "Separate topics with commas.",
              },
              h("input", {
                id: "avoid",
                value: v.npAvoid,
                onChange: v.onAvoid,
                placeholder: "Sponsor reads, personal details",
              }),
            ),
          ),
        ),
      h(
        "div",
        { className: "form-footer" },
        h(
          "p",
          null,
          v.allowanceText ||
            "Your source is kept for 24 hours. Analysis is kept for 30 days.",
        ),
        h(
          Button,
          {
            primary: true,
            icon: "spark",
            onClick: v.start,
            disabled: v.cannotStart,
          },
          v.npBusy
            ? v.isUrlTab
              ? "Downloading video…"
              : "Preparing upload…"
            : "Find my Shorts",
        ),
      ),
      v.npBusy &&
        h(
          "p",
          {
            className: "small muted mt-4",
            role: "status",
          },
          "Keep this page open while your source video is transferred.",
        ),
    );
  }
  function ProcessingStatus({ stages }) {
    return h(
      "ol",
      { className: "stages", "aria-label": "Processing steps" },
      stages.map((s) =>
        h(
          "li",
          {
            key: s.n,
            className: `stage ${s.stateText === "Done" ? "done" : s.stateText === "In progress" ? "active" : s.stateText === "Failed" ? "failed" : ""}`,
            "aria-current": s.stateText === "In progress" ? "step" : undefined,
          },
          h(
            "span",
            { className: "stage-circle", "aria-hidden": true },
            s.stateText === "Done"
              ? h(Icon, { name: "check" })
              : s.stateText === "In progress"
                ? h("span", { className: "activity-dot" })
                : s.stateText === "Failed"
                  ? "!"
                  : s.n,
          ),
          h(
            "span",
            null,
            s.stateText === "In progress"
              ? { Uploaded: "Uploading", Transcribed: "Transcribing" }[
                  s.label
                ] || s.label
              : s.label,
          ),
          h("span", { className: "stage-meta" }, s.stateText),
        ),
      ),
    );
  }
  function Processing({ v }) {
    return h(
      "div",
      { className: "narrow" },
      h(
        PageHeader,
        {
          title: v.isDone
            ? "Your moments are ready"
            : "Finding your next Short",
          description: "We’re finding the strongest moments in your video.",
        },
        h(Button, { ghost: true, onClick: v.goDashboard }, "Back to dashboard"),
      ),
      h(
        "section",
        { className: "panel processing-panel" },
        h(SourceSummary, {
          title: v.projName || "Your video",
          url: v.sourceUrl,
          detail: v.projectDuration,
        }),
        h(
          "div",
          {
            className: "processing-title",
            role: "status",
            "aria-live": "polite",
          },
          h(
            "h2",
            null,
            v.isDone
              ? "Ready for your review"
              : v.hasProcError
                ? "We couldn't process this video."
                : v.statusKey === "canceled"
                  ? "Processing canceled"
                  : v.statusText,
          ),
          h(
            "p",
            { className: "muted" },
            v.isDone
              ? "Explore your suggested clips and choose the ones you want to publish."
              : v.statusKey === "analyzing"
                ? "Analyzing your transcript for strong hooks and standalone moments."
                : v.statusKey === "uploading"
                  ? "Keep this page open while your video uploads."
                  : "You can return to your dashboard. Your project will keep processing.",
          ),
        ),
        h(ProcessingStatus, { stages: v.stages }),
        v.hasProcError &&
          h(ErrorState, {
            title: "Your video needs another try.",
            message:
              v.procErrorText ||
              "Try again, or create a project with a different video file.",
            retry: v.canRetry ? v.retry : undefined,
          }),
        h(
          "div",
          { className: "row wrap" },
          v.isDone &&
            h(
              Button,
              { primary: true, icon: "arrow", onClick: v.goSuggestions },
              "Review best moments",
            ),
          v.isDone && h(Button, { onClick: v.goTranscript }, "Edit transcript"),
          v.canCancel &&
            h(Button, { ghost: true, onClick: v.cancel }, "Cancel processing"),
          v.canRetry &&
            !v.hasProcError &&
            h(Button, { primary: true, onClick: v.retry }, "Try again"),
        ),
      ),
    );
  }
  function ScoreIndicator({ label, value }) {
    const number = Math.max(0, Math.min(100, Number(value) || 0));
    return h(
      "div",
      { className: "score", "aria-label": `${label}: ${number} out of 100` },
      label
        .replace("Standalone clarity", "Clarity")
        .replace("Visual quality", "Visual"),
      h("b", null, number),
      h(
        "div",
        { className: "score-track", "aria-hidden": true },
        h("div", { className: "score-fill", style: { width: `${number}%` } }),
      ),
    );
  }
  function ClipCard({ clip }) {
    return h(
      "article",
      { className: `panel clip-card ${clip.rank === 1 ? "best" : ""}` },
      h(
        "div",
        { className: "clip-visual" },
        clip.hasVideo
          ? h("video", {
              src: clip.videoUrl,
              muted: true,
              preload: "metadata",
              playsInline: true,
              "aria-label": `Thumbnail for ${clip.hook}`,
            })
          : h(
              "div",
              { className: "thumb-placeholder" },
              h(Icon, { name: "video" }),
              "Source preview unavailable",
            ),
        h(
          "div",
          { className: "clip-badges" },
          h(
            "span",
            { className: `badge ${clip.rank === 1 ? "accent" : ""}` },
            clip.rank === 1
              ? "✦ Top pick"
              : `Moment ${String(clip.rank).padStart(2, "0")}`,
          ),
          h("span", { className: "badge" }, clip.durationLabel),
        ),
      ),
      h(
        "div",
        { className: "clip-body" },
        h(
          "div",
          null,
          h("h3", null, clip.hook),
          h(
            "div",
            { className: "clip-time mt-2" },
            h(Icon, { name: "clock", className: "icon icon-xs" }),
            clip.range,
          ),
        ),
        h("p", { className: "reason" }, clip.reason),
        h(
          "div",
          { className: "scores", "aria-label": "Clip scores" },
          clip.scores.map((s) => h(ScoreIndicator, { key: s.label, ...s })),
        ),
        h(
          "div",
          { className: "clip-actions" },
          h(Button, { icon: "play", onClick: clip.preview }, "Preview"),
          h(Button, { primary: true, onClick: clip.preview }, "Create Short"),
        ),
        h(
          "label",
          { className: "check small" },
          h("input", {
            type: "checkbox",
            checked: clip.selected,
            onChange: clip.select,
          }),
          "Select clip",
        ),
      ),
    );
  }
  function Suggestions({ v }) {
    return h(
      root.React.Fragment,
      null,
      h(
        PageHeader,
        {
          title: v.clipsLoaded
            ? `We found ${v.clips.length} potential ${v.clips.length === 1 ? "Short" : "Shorts"}`
            : "Finding your best moments",
          description: v.projName,
        },
        h(Button, { onClick: v.goTranscript }, "Edit transcript"),
        h(
          Button,
          { ghost: true, onClick: () => v.navigate("projects") },
          "All projects",
        ),
      ),
      v.clipsStale &&
        h(
          "div",
          { className: "notice mb-5" },
          h(Icon, { name: "alert" }),
          "Your transcript changed. Regenerate suggestions to include your edits.",
        ),
      h(
        "details",
        {
          className: "refine",
          open: v.clipsLoaded && !v.clips.length ? true : undefined,
        },
        h("summary", null, "Refine your suggestions"),
        h(
          "div",
          { className: "stack" },
          h(Segments, { items: v.regenGoals, label: "Regeneration goal" }),
          h(
            "div",
            { className: "refine-grid" },
            h(
              Field,
              {
                id: "regen-instruction",
                label: "What should the AI look for?",
                optional: true,
              },
              h("input", {
                id: "regen-instruction",
                value: v.regenInstruction,
                onChange: v.onRegenInstruction,
                maxLength: 200,
                placeholder: "A new angle or topic…",
              }),
            ),
            h(
              Field,
              { id: "regen-avoid", label: "Topics to avoid", optional: true },
              h("input", {
                id: "regen-avoid",
                value: v.regenAvoid,
                onChange: v.onRegenAvoid,
                placeholder: "Separate topics with commas",
              }),
            ),
          ),
          h(
            "div",
            { className: "row between wrap" },
            h(
              "span",
              { className: "small muted" },
              "Creates a new set of suggestions from your transcript.",
            ),
            h(
              Button,
              {
                onClick: v.regenerate,
                disabled: v.regenerating,
                icon: "spark",
              },
              v.regenerating
                ? "Finding new moments…"
                : "Regenerate suggestions",
            ),
          ),
        ),
      ),
      v.regenerating &&
        h(
          "div",
          { className: "notice mb-5", role: "status" },
          h("span", { className: "activity-dot" }),
          "Looking for new moments. Your current suggestions are available below.",
        ),
      v.clipsError
        ? h(ErrorState, {
            title: "We couldn't load your suggestions.",
            retry: v.reloadClips,
          })
        : !v.clipsLoaded
          ? h(Skeleton, { cards: true })
          : v.clips.length
            ? h(
                "div",
                { className: "clip-grid" },
                v.clips.map((c) => h(ClipCard, { key: c.id, clip: c })),
              )
            : h(
                "div",
                { className: "panel" },
                h(EmptyState, {
                  title: "No moments found yet",
                  description:
                    "Try a different clip goal or give the AI a little more direction.",
                  icon: "spark",
                }),
              ),
      v.hasRenderedClips &&
        h(
          "section",
          null,
          h(
            "div",
            { className: "section-head" },
            h("h2", null, "Rendered Shorts"),
            h(
              "span",
              { className: "small muted" },
              `${v.renderedClips.length} ready to download`,
            ),
          ),
          h(
            "div",
            { className: "render-list" },
            v.renderedClips.map((r, index) =>
              h(
                "div",
                { className: "panel render-row", key: r.filename },
                h(
                  "div",
                  { className: "row" },
                  h(SourceThumb, { url: r.url }),
                  h(
                    "div",
                    { className: "grow" },
                    h(
                      "h3",
                      { className: "truncate" },
                      r.hook || `Short ${index + 1}`,
                    ),
                    h(
                      "p",
                      { className: "small muted" },
                      `${r.bytesLabel} · MP4`,
                    ),
                  ),
                ),
                h(
                  Button,
                  { onClick: () => v.openResult(r), icon: "play" },
                  "View Short",
                ),
              ),
            ),
          ),
        ),
    );
  }
  function VideoPreview({ url, label = "Video preview", end, start = 0 }) {
    const [failed, setFailed] = root.React.useState(false);
    const [ready, setReady] = root.React.useState(false);
    root.React.useEffect(() => {
      setFailed(false);
      setReady(false);
    }, [url]);
    return h(
      "div",
      { className: "video-preview" },
      url && !failed
        ? h("video", {
            key: url,
            src: url,
            controls: true,
            playsInline: true,
            preload: "auto",
            onLoadedData: () => setReady(true),
            onCanPlay: () => setReady(true),
            "aria-label": label,
            onError: () => setFailed(true),
            onPlay: (e) => {
              document.querySelectorAll("video").forEach((video) => {
                if (video !== e.currentTarget) video.pause();
              });
              if (
                end &&
                (e.currentTarget.currentTime >= end ||
                  e.currentTarget.currentTime < start)
              )
                e.currentTarget.currentTime = start;
            },
            onTimeUpdate: (e) => {
              if (end && e.currentTarget.currentTime >= end) {
                e.currentTarget.pause();
                e.currentTarget.currentTime = start;
              }
            },
          })
        : h(
            "div",
            { className: "video-unavailable" },
            h(Icon, { name: "video" }),
            h(
              "strong",
              null,
              failed ? "Preview could not load" : "Source preview unavailable",
            ),
            h(
              "span",
              null,
              failed
                ? "Go back and reopen this project to refresh the video link."
                : "This source may have expired. Upload the video again to preview and render it.",
            ),
          ),
      url &&
        !failed &&
        !ready &&
        h(
          "div",
          { className: "video-loading", role: "status" },
          h(Icon, { name: "video" }),
          h("span", null, "Loading preview…"),
        ),
    );
  }
  function CaptionPresetCard({ label, kind }) {
    return h(
      "button",
      {
        type: "button",
        disabled: true,
        className: "preset",
        "aria-label": `${label} caption style — not available yet`,
      },
      h(
        "div",
        { className: "preset-art" },
        kind === "bold"
          ? h("strong", null, "YOUR NEXT IDEA")
          : kind === "highlight"
            ? h("span", null, "Your next ", h("em", null, "idea"))
            : h("span", null, "Your next idea"),
      ),
      h("span", null, label),
    );
  }
  function Editor({ v }) {
    const pv = v.pv;
    return h(
      root.React.Fragment,
      null,
      h(
        PageHeader,
        { title: "Make it a Short", description: v.projName },
        h(
          Button,
          { ghost: true, icon: "back", onClick: v.goSuggestions },
          "Back to moments",
        ),
      ),
      h(
        "div",
        { className: "editor" },
        h(
          "div",
          { className: "preview-stage" },
          h(VideoPreview, {
            url: pv.videoUrl,
            label: pv.hook,
            start: pv.start,
            end: pv.end,
          }),
          h(
            "p",
            { className: "preview-foot" },
            `${pv.range || ""} · ${pv.duration || ""} clip`,
          ),
          h("p", { className: "preview-foot" }, "Fits the whole video · 9:16"),
          pv.reason &&
            h(
              "div",
              { className: "preview-context" },
              h("h3", null, "Why this moment"),
              h("p", null, pv.reason),
            ),
        ),
        h(
          "aside",
          {
            className: "panel editor-controls",
            "aria-label": "Short settings",
          },
          h(
            "section",
            { className: "stack compact-stack" },
            h(
              "div",
              { className: "row between" },
              h("h2", null, "Suggested hook"),
              h(
                Button,
                {
                  ghost: true,
                  small: true,
                  icon: "copy",
                  onClick: () => v.copy(pv.hook),
                  "aria-label": "Copy hook",
                },
                "Copy",
              ),
            ),
            h("p", { className: "hook-text" }, pv.hook),
            h(
              "p",
              { className: "small muted" },
              "A starting point for your title or caption.",
            ),
          ),
          h(
            "section",
            { className: "field" },
            h("h2", null, "Output quality"),
            h(Segments, {
              label: "Output resolution",
              items: [1080, 720].map((n) => ({
                label: `${n}p`,
                pressed: String(v.resolution === n),
                pick: () => v.setResolution(n),
                disabled: !!v.renderingId,
              })),
            }),
          ),
          h(
            "div",
            { className: "row between" },
            h("span", { className: "small muted" }, "Transcript corrections"),
            h(
              Button,
              { ghost: true, small: true, onClick: v.goTranscript },
              "Edit transcript",
            ),
          ),
          h(
            "div",
            { className: "render-footer" },
            v.renderError &&
              h(ErrorState, {
                title: "We couldn't render this Short.",
                message: v.renderError,
              }),
            h(
              Button,
              {
                primary: true,
                className: "wide",
                icon: "spark",
                onClick: v.renderPreview,
                disabled: !!v.renderingId || v.sourceExpired,
              },
              v.renderingId ? "Rendering your Short…" : "Render Short",
            ),
            h(
              "p",
              { role: v.renderingId ? "status" : undefined },
              v.sourceExpired
                ? "The source expired. Upload it again to render."
                : v.renderingId
                  ? "Keep this page open. We’re preparing your MP4."
                  : "MP4 · Ready for Shorts, TikTok & Reels",
            ),
          ),
          h(
            "details",
            { className: "future-options" },
            h(
              "summary",
              null,
              h("span", null, "Captions & branding"),
              h("span", { className: "badge" }, "Coming soon"),
            ),
            h(
              "div",
              { className: "stack compact-stack" },
              h(
                "p",
                { className: "small muted" },
                "Text overlays, caption styles, and creator watermarks aren’t available yet.",
              ),
              h(
                "div",
                { className: "field" },
                h("span", { className: "field-label" }, "Caption style"),
                h(
                  "div",
                  { className: "preset-grid" },
                  h(CaptionPresetCard, { label: "Clean", kind: "clean" }),
                  h(CaptionPresetCard, { label: "Bold", kind: "bold" }),
                  h(CaptionPresetCard, {
                    label: "Highlight",
                    kind: "highlight",
                  }),
                ),
              ),
              h(
                Field,
                { id: "watermark", label: "Creator watermark", optional: true },
                h("input", {
                  id: "watermark",
                  placeholder: "@yourname",
                  disabled: true,
                }),
              ),
            ),
          ),
        ),
      ),
    );
  }
  function Result({ v }) {
    const [platform, setPlatform] = root.React.useState("YouTube");
    const r = v.result || {};
    return h(
      root.React.Fragment,
      null,
      h(
        "div",
        { className: "row between mb-5" },
        h(
          Button,
          { ghost: true, icon: "back", onClick: v.goSuggestions },
          "Back to moments",
        ),
        h(Button, { onClick: v.goNew, icon: "plus" }, "New Project"),
      ),
      h(
        "header",
        { className: "result-heading" },
        h("div", { className: "success-symbol" }, h(Icon, { name: "check" })),
        h("h1", { id: "page-title", tabIndex: -1 }, "Your Short is ready"),
        h("p", null, "One great moment. Ready for your audience."),
      ),
      h(
        "div",
        { className: "editor" },
        h(
          "div",
          { className: "preview-stage" },
          h(VideoPreview, { url: r.url, label: "Rendered Short" }),
          h(
            "p",
            { className: "preview-foot" },
            `${r.resolution ? `${r.resolution}p · ` : ""}MP4${r.bytes ? ` · ${(r.bytes / 1e6).toFixed(1)} MB` : ""}`,
          ),
        ),
        h(
          "div",
          { className: "stack" },
          h(
            "section",
            { className: "panel pad stack" },
            h("h2", null, "Download your Short"),
            h(
              "a",
              {
                href: r.downloadUrl || r.url,
                download: r.filename,
                className: "btn primary wide",
              },
              h(Icon, { name: "download" }),
              "Download Video",
            ),
            h(
              "details",
              { className: "additional-downloads" },
              h(
                "summary",
                null,
                h("span", null, "Cover & subtitle files"),
                h("span", { className: "badge" }, "Coming soon"),
              ),
              h(
                "div",
                { className: "download-grid" },
                ["Cover", "SRT", "ASS"].map((kind) =>
                  h(
                    Button,
                    {
                      key: kind,
                      disabled: true,
                      small: true,
                      title: "Not available yet",
                    },
                    `Download ${kind}`,
                  ),
                ),
              ),
              h(
                "p",
                { className: "small muted mt-2" },
                "Cover and subtitle downloads aren’t available yet.",
              ),
            ),
          ),
          h(
            "section",
            { className: "panel pad stack" },
            h("h2", null, "Prepare to publish"),
            h(
              "div",
              {
                className: "segmented",
                role: "tablist",
                "aria-label": "Publishing platform",
              },
              ["YouTube", "TikTok", "Instagram"].map((p, i) =>
                h(
                  "button",
                  {
                    key: p,
                    className: "segment",
                    role: "tab",
                    id: `platform-${p}`,
                    "aria-controls": "platform-content",
                    "aria-selected": platform === p,
                    tabIndex: platform === p ? 0 : -1,
                    onClick: () => setPlatform(p),
                    onKeyDown: (e) => {
                      const list = ["YouTube", "TikTok", "Instagram"];
                      const next =
                        e.key === "ArrowRight"
                          ? (i + 1) % 3
                          : e.key === "ArrowLeft"
                            ? (i + 2) % 3
                            : e.key === "Home"
                              ? 0
                              : e.key === "End"
                                ? 2
                                : null;
                      if (next !== null) {
                        e.preventDefault();
                        setPlatform(list[next]);
                        document
                          .getElementById(`platform-${list[next]}`)
                          .focus();
                      }
                    },
                  },
                  p,
                ),
              ),
            ),
            h(
              "div",
              {
                role: "tabpanel",
                id: "platform-content",
                "aria-labelledby": `platform-${platform}`,
                className: "stack",
              },
              r.hook &&
                h(
                  "div",
                  { className: "field" },
                  h(
                    "span",
                    { className: "field-label" },
                    platform === "YouTube"
                      ? "Suggested title"
                      : "Suggested caption",
                  ),
                  h("p", null, r.hook),
                  h(
                    Button,
                    {
                      small: true,
                      icon: "copy",
                      onClick: () => v.copy(r.hook),
                    },
                    platform === "YouTube" ? "Copy title" : "Copy caption",
                  ),
                ),
              h(
                "p",
                { className: "small muted" },
                "Add your own description and hashtags when publishing.",
              ),
            ),
          ),
        ),
      ),
    );
  }
  function Transcript({ v }) {
    return h(
      root.React.Fragment,
      null,
      h(
        PageHeader,
        {
          title: "Edit transcript",
          description:
            "Correct names or wording, then regenerate your suggestions.",
        },
        h(
          Button,
          { onClick: v.downloadTxt, disabled: !v.transcriptLoaded },
          "Download TXT",
        ),
        h(
          Button,
          { onClick: v.downloadJson, disabled: !v.transcriptLoaded },
          "Download JSON",
        ),
      ),
      v.transcriptError
        ? h(ErrorState, {
            title: "We couldn't load the transcript.",
            retry: v.goTranscript,
          })
        : !v.transcriptLoaded
          ? h(Skeleton)
          : h(
              "div",
              { className: "panel" },
              v.segments.map((seg, i) =>
                h(
                  "div",
                  { key: i, className: "transcript-row" },
                  h(
                    "div",
                    { className: "transcript-time" },
                    seg.time,
                    seg.edited &&
                      h("div", { className: "badge accent" }, "Edited"),
                  ),
                  h("textarea", {
                    "aria-label": seg.label,
                    value: seg.text,
                    onChange: seg.onChange,
                  }),
                ),
              ),
            ),
      h(
        "div",
        { className: "transcript-actions row between" },
        h(
          "span",
          { className: "small muted" },
          `Transcript revision ${v.revision}`,
        ),
        h(
          "div",
          { className: "row wrap" },
          h(
            Button,
            {
              disabled: v.notDirty || v.savingTranscript,
              onClick: v.saveTranscript,
            },
            v.savingTranscript ? "Saving…" : v.saveLabel,
          ),
          h(
            Button,
            { primary: true, onClick: v.goSuggestions },
            "Back to moments",
          ),
        ),
      ),
    );
  }
  function Usage({ v }) {
    return h(
      "div",
      { className: "narrow" },
      h(PageHeader, {
        title: "Usage",
        description: "Processing minutes for your current billing month.",
      }),
      h(
        "section",
        { className: "panel pad stack" },
        h(
          "div",
          { className: "row between" },
          h("h2", null, "Video processing"),
          h("span", { className: "badge" }, v.monthLabel),
        ),
        h(
          "div",
          { className: "usage-big" },
          v.usageUsed,
          h(
            "span",
            null,
            v.isUnlimitedUsage
              ? " min used · unlimited plan"
              : ` / ${v.usageLimit} min used`,
          ),
        ),
        !v.isUnlimitedUsage &&
          h(
            "div",
            { className: "meter" },
            h("div", {
              className: "meter-fill",
              style: { width: `${v.usagePct}%` },
            }),
          ),
        h(
          "p",
          { className: "small muted" },
          v.isUnlimitedUsage
            ? "Your account has no monthly processing cap."
            : `${v.usageRemaining} processing minutes remaining. Resets ${v.resetLabel}.`,
        ),
      ),
      h(
        "div",
        { className: "section-head" },
        h("h2", null, "Processing activity"),
      ),
      v.usageError
        ? h(ErrorState, {
            title: "We couldn't load usage activity.",
            retry: v.loadUsage,
          })
        : v.usageLoading
          ? h(Skeleton)
          : h(
              "div",
              { className: "panel" },
              v.usageEntries.length
                ? v.usageEntries.map((entry, i) =>
                    h(
                      "div",
                      { className: "settings-row", key: i },
                      h(
                        "div",
                        null,
                        h("h3", null, entry.title),
                        h("p", null, entry.date),
                      ),
                      h("span", { className: "small" }, entry.minutes),
                    ),
                  )
                : h(EmptyState, {
                    title: "No processing activity yet",
                    description:
                      "Your processing history will appear here when you work on a video.",
                    icon: "usage",
                  }),
            ),
    );
  }
  function Settings({ v }) {
    return h(
      "div",
      { className: "narrow" },
      h(PageHeader, {
        title: "Settings",
        description: "Your account and workspace preferences.",
      }),
      h(
        "section",
        { className: "panel" },
        h(
          "div",
          { className: "settings-row" },
          h("div", null, h("h3", null, "Profile"), h("p", null, v.email)),
          h(
            "div",
            { className: "row" },
            h("span", { className: "avatar" }, v.initials),
            h("strong", { className: "small" }, v.userName),
          ),
        ),
        h(
          "div",
          { className: "settings-row" },
          h(
            "div",
            null,
            h("h3", null, "Appearance"),
            h("p", null, "Saved on this browser."),
          ),
          h(Segments, {
            label: "Color theme",
            items: ["light", "dark", "system"].map((mode) => ({
              label: mode[0].toUpperCase() + mode.slice(1),
              pressed: String(v.theme === mode),
              pick: () => v.setTheme(mode),
            })),
          }),
        ),
        h(
          "div",
          { className: "settings-row" },
          h(
            "div",
            null,
            h("h3", null, "File retention"),
            h(
              "p",
              null,
              "Source video: 24 hours. Renders: 7 days. Analysis: 30 days.",
            ),
          ),
          h("span", { className: "badge" }, "Download to keep"),
        ),
        h(
          "div",
          { className: "settings-row" },
          h(
            "div",
            null,
            h("h3", null, "Session"),
            h("p", null, "Sign out of this browser."),
          ),
          h(Button, { icon: "logout", onClick: v.signOut }, "Sign out"),
        ),
      ),
      h(
        "details",
        { className: "advanced-settings mt-5" },
        h("summary", null, "Advanced"),
        h(
          "a",
          {
            href: "Service%20Tests.dc.html",
            target: "_blank",
            rel: "noopener",
            className: "small",
          },
          "Service diagnostics",
        ),
      ),
    );
  }
  function Login({ v }) {
    return h(
      "div",
      { className: "login" },
      h(
        "aside",
        { className: "login-story" },
        h(Brand),
        h(
          "div",
          { className: "login-message" },
          h(
            "span",
            { className: "eyebrow" },
            "Made for your next great moment",
          ),
          h("h1", null, "Long conversations. Lasting impressions."),
          h(
            "p",
            null,
            "Turn the best parts of your videos into Shorts worth sharing. Find the moment, make it yours, and publish.",
          ),
        ),
        h(
          "div",
          { className: "workflow" },
          h("span", null, "01 Upload"),
          h("span", null, "02 Discover"),
          h("span", null, "03 Create"),
        ),
      ),
      h(
        "main",
        { className: "login-form" },
        h(
          "form",
          { onSubmit: v.signIn },
          h("div", { className: "mobile-brand" }, h(Brand)),
          h("h2", null, "Welcome back"),
          h("p", { className: "muted" }, "Sign in to your creative workspace."),
          v.loginError &&
            h(ErrorState, {
              title: "Unable to sign in",
              message: v.loginError,
            }),
          h(
            Field,
            { id: "email", label: "Email address" },
            h("input", {
              id: "email",
              name: "email",
              type: "email",
              required: true,
              autoComplete: "username",
              value: v.loginEmail,
              onChange: v.onLoginEmail,
              placeholder: "you@example.com",
            }),
          ),
          h(
            Field,
            { id: "password", label: "Password" },
            h("input", {
              id: "password",
              name: "password",
              type: "password",
              required: true,
              autoComplete: "current-password",
              value: v.loginPassword,
              onChange: v.onLoginPassword,
            }),
          ),
          h(
            Button,
            {
              type: "submit",
              primary: true,
              className: "wide",
              disabled: v.loginBusy,
            },
            v.loginLabel,
          ),
          h(
            "p",
            { className: "login-caption" },
            "Invite-only access. Use the credentials provided by your workspace owner.",
          ),
        ),
      ),
    );
  }
  function DeleteDialog({ project, close, confirm, busy }) {
    const ref = root.React.useRef(null);
    root.React.useEffect(() => {
      const previous = document.activeElement;
      ref.current?.querySelector("button")?.focus();
      return () => previous?.focus();
    }, []);
    return h(
      "div",
      {
        className: "overlay",
        onClick: (e) => {
          if (e.target === e.currentTarget && !busy) close();
        },
      },
      h(
        "section",
        {
          ref,
          className: "panel dialog",
          role: "dialog",
          "aria-modal": true,
          "aria-labelledby": "delete-title",
          onKeyDown: (e) => {
            if (e.key === "Escape" && !busy) close();
            if (e.key === "Tab") {
              const buttons = [
                ...ref.current.querySelectorAll("button:not(:disabled)"),
              ];
              const first = buttons[0],
                last = buttons[buttons.length - 1];
              if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
              } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
              }
            }
          },
        },
        h("h2", { id: "delete-title" }, "Delete this project?"),
        h(
          "p",
          null,
          `“${project.name}” and its saved work will be removed. This cannot be undone.`,
        ),
        h(
          "div",
          { className: "row end" },
          h(Button, { onClick: close, disabled: busy }, "Keep project"),
          h(
            Button,
            { className: "danger", onClick: confirm, disabled: busy },
            busy ? "Deleting…" : "Delete project",
          ),
        ),
      ),
    );
  }
  function App({ v }) {
    const [open, setOpen] = root.React.useState(false);
    const [deleting, setDeleting] = root.React.useState(null);
    const [deleteBusy, setDeleteBusy] = root.React.useState(false);
    root.React.useEffect(() => {
      setOpen(false);
      document.title = `${v.pageLabel} · Shorts Studio`;
      if (!["login", "boot"].includes(v.route))
        document.getElementById("page-title")?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }, [v.route]);
    root.React.useEffect(() => {
      const onKey = (e) => {
        if (e.key === "Escape") setOpen(false);
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }, []);
    if (v.isBoot)
      return h(
        "main",
        { className: "narrow pad boot-screen" },
        h(Brand),
        h(
          "div",
          { className: "mt-6 stack" },
          v.bootError
            ? h(
                root.React.Fragment,
                null,
                h(ErrorState, {
                  title: "We couldn’t restore your session.",
                  message: v.bootError,
                  retry: v.restoreSession,
                }),
                h(
                  Button,
                  { ghost: true, onClick: v.showLogin },
                  "Go to sign in",
                ),
              )
            : h(Skeleton),
        ),
      );
    if (v.isLogin) return h(Login, { v });
    const screens = {
      dashboard: Dashboard,
      projects: Projects,
      new: NewProject,
      processing: Processing,
      suggestions: Suggestions,
      editor: Editor,
      result: Result,
      transcript: Transcript,
      usage: Usage,
      settings: Settings,
    };
    return h(
      "div",
      { className: `shell ${open ? "nav-open" : ""}` },
      h("a", { className: "skip", href: "#main-content" }, "Skip to content"),
      h(AppSidebar, { v, open, close: () => setOpen(false) }),
      h(
        "header",
        { className: "topbar" },
        h(
          "div",
          { className: "row" },
          h(Button, {
            ghost: true,
            icon: "menu",
            className: "mobile-menu icon-btn",
            onClick: () => setOpen(!open),
            "aria-label": "Open navigation",
            "aria-expanded": open,
            "aria-controls": "app-sidebar",
          }),
          h(
            "div",
            { className: "breadcrumb" },
            h("span", { className: "hide-mobile" }, "Workspace"),
            h("span", { className: "hide-mobile", "aria-hidden": true }, "/"),
            h("strong", null, v.pageLabel),
          ),
        ),
        h(UsageIndicator, {
          used: v.usageUsed,
          limit: v.usageLimit,
          compact: true,
        }),
      ),
      h(
        "main",
        { className: "main", id: "main-content", tabIndex: -1 },
        h(screens[v.route] || Dashboard, { v, onDelete: setDeleting }),
      ),
      v.toast &&
        h(
          "div",
          { className: "toast", role: "status", "aria-live": "polite" },
          v.toast,
        ),
      deleting &&
        h(DeleteDialog, {
          project: deleting,
          close: () => setDeleting(null),
          busy: deleteBusy,
          confirm: async () => {
            setDeleteBusy(true);
            const ok = await v.deleteProject(deleting.id);
            setDeleteBusy(false);
            if (ok) setDeleting(null);
          },
        }),
    );
  }
  root.ShortsUI = {
    App,
    AppSidebar,
    PageHeader,
    ProjectTable,
    ClipCard,
    ScoreIndicator,
    VideoPreview,
    ProcessingStatus,
    UploadDropzone,
    CaptionPresetCard,
    UsageIndicator,
    EmptyState,
    ErrorState,
    StatusBadge,
  };
})(window);
