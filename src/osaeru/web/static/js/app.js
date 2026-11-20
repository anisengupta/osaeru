// Osaeru client-side bits: the flowchart, and small chat conveniences.
// Pages are server-rendered; htmx swaps the <body> when you move between
// tabs, so everything here re-runs from initPage() after each swap.

(function () {
  "use strict";

  let cy = null;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ---- flowchart ----------------------------------------------------------
  function chartStyle() {
    return [
      {
        selector: "node",
        style: {
          shape: "round-rectangle",
          width: 172,
          height: 60,
          label: "data(label)",
          "text-wrap": "wrap",
          "text-max-width": 152,
          "text-valign": "center",
          "text-halign": "center",
          "font-family": cssVar("--font"),
          "font-size": 13,
          "font-weight": 500,
          color: cssVar("--node-ink"),
          "background-color": cssVar("--c-unrated"),
          "border-width": 0,
          "transition-property": "opacity",
          "transition-duration": "150ms",
        },
      },
      { selector: 'node[confidence = "learning"]', style: { "background-color": cssVar("--c-learning") } },
      { selector: 'node[confidence = "comfortable"]', style: { "background-color": cssVar("--c-comfortable") } },
      { selector: 'node[confidence = "reliable"]', style: { "background-color": cssVar("--c-reliable") } },
      {
        selector: "edge",
        style: {
          width: 1.75,
          "line-color": cssVar("--muted"),
          "target-arrow-color": cssVar("--muted"),
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.9,
          "curve-style": "taxi",
          "taxi-direction": "downward",
          "taxi-turn": "50%",
          "transition-property": "opacity",
          "transition-duration": "150ms",
        },
      },
      {
        selector: 'edge[relation = "counters"]',
        style: {
          "curve-style": "unbundled-bezier",
          "line-style": "dashed",
          "line-color": cssVar("--red"),
          "target-arrow-color": cssVar("--red"),
        },
      },
      { selector: "node:selected", style: { "border-width": 4, "border-color": cssVar("--ink") } },
      { selector: ".faded", style: { opacity: 0.18 } },
    ];
  }

  function runLayout() {
    // Counters edges are sideways relationships, so they don't decide rows.
    cy.elements()
      .not('edge[relation = "counters"]')
      .layout({
        name: "dagre",
        rankDir: "TB",
        nodeSep: 28,
        rankSep: 64,
        edgeSep: 12,
        fit: true,
        padding: 28,
        animate: false,
      })
      .run();
  }

  function listSection(panel, title, items) {
    if (!items.length) return;
    const h = document.createElement("h3");
    h.textContent = title;
    const ul = document.createElement("ul");
    items.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      ul.appendChild(li);
    });
    panel.append(h, ul);
  }

  function showDetail(node) {
    const panel = document.getElementById("chart-detail");
    if (!panel) return;
    panel.replaceChildren();

    const d = node.data();
    const title = document.createElement("h2");
    title.textContent = d.label;
    const meta = document.createElement("p");
    meta.className = "detail-meta";
    const confidence = d.confidence === "unrated" ? "No confidence rating yet" : d.confidence[0].toUpperCase() + d.confidence.slice(1);
    meta.textContent = d.category ? `${confidence}, ${d.category}` : confidence;
    panel.append(title, meta);

    const flow = node.connectedEdges().not('[relation = "counters"]');
    listSection(panel, "Leads to", flow.filter((e) => e.source().same(node)).map((e) => e.target().data("label")));
    listSection(panel, "Comes from", flow.filter((e) => e.target().same(node)).map((e) => e.source().data("label")));
    listSection(
      panel,
      "Counter relationships",
      node.connectedEdges('[relation = "counters"]').map((e) =>
        e.source().same(node) ? `Counters ${e.target().data("label")}` : `Countered by ${e.source().data("label")}`
      )
    );
    listSection(panel, "To try", d.recommendations || []);
  }

  function resetDetail() {
    const panel = document.getElementById("chart-detail");
    if (!panel) return;
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Tap a skill to see how it connects.";
    panel.replaceChildren(p);
  }

  async function initChart() {
    const container = document.getElementById("chart");
    if (cy && (!container || cy.container() !== container)) {
      cy.destroy();
      cy = null;
    }
    if (!container || cy || typeof cytoscape === "undefined") return;

    let data;
    try {
      const res = await fetch(container.dataset.src, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      container.textContent = `Couldn't load the flowchart (${err.message}). Refresh to try again.`;
      return;
    }

    cy = cytoscape({
      container,
      elements: [...data.nodes, ...data.edges],
      style: chartStyle(),
      minZoom: 0.2,
      maxZoom: 2.5,
      wheelSensitivity: 0.3,
      autoungrabify: true, // tap and pan, but don't drag nodes out of the layout
    });
    runLayout();

    cy.on("tap", "node", (evt) => {
      const node = evt.target;
      cy.elements().addClass("faded");
      node.closedNeighborhood().removeClass("faded");
      showDetail(node);
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("faded");
        resetDetail();
      }
    });
  }

  // ---- chat ---------------------------------------------------------------
  function initChat() {
    const log = document.getElementById("chat-log");
    if (log) log.scrollTop = log.scrollHeight;

    const box = document.getElementById("message");
    if (box && !box.dataset.bound) {
      box.dataset.bound = "1";
      // Cmd/Ctrl + Enter sends; plain Enter stays a newline.
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          box.form.requestSubmit();
        }
      });
    }
  }

  function initPage() {
    initChart();
    initChat();
  }

  document.addEventListener("DOMContentLoaded", initPage);
  document.addEventListener("htmx:afterSettle", initPage);
  document.addEventListener("htmx:beforeRequest", (e) => {
    // Show the typing indicator straight away when a chat message is sent.
    if (e.target && e.target.classList && e.target.classList.contains("chat-form")) {
      const log = document.getElementById("chat-log");
      if (log) requestAnimationFrame(() => (log.scrollTop = log.scrollHeight));
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-chart-fit]") && cy) cy.fit(undefined, 28);
  });
})();
