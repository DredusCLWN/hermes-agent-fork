/**
 * Graphify dashboard plugin — Code Graph page.
 *
 * Uses window.__HERMES_PLUGIN_SDK__ for React, UI components, and API.
 * Registers itself via window.__HERMES_PLUGINS__.register("graphify", Component).
 *
 * Interactive dependency graph visualization using vis-network.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useRef = SDK.hooks.useRef;
  var useCallback = SDK.hooks.useCallback;
  var fetchJSON = SDK.fetchJSON;
  var Button = SDK.components.Button;
  var Input = SDK.components.Input;
  var cn = SDK.utils.cn;

  var API_BASE = "/api/plugins/graphify";

  // -- Status polling hook --------------------------------------------------

  function useGraphStatus() {
    var state = useState({ status: "loading", progress: 0, error: null, node_count: 0, edge_count: 0, community_count: 0, graph_exists: false });
    var status = state[0];
    var setStatus = state[1];

    useEffect(function () {
      // Immediate fetch
      fetchJSON(API_BASE + "/status").then(function (data) {
        setStatus(data);
      }).catch(function () {
        setStatus({ status: "error", error: "Failed to connect to graphify API", progress: 0, node_count: 0, edge_count: 0, community_count: 0, graph_exists: false });
      });

      // Poll every 2s while building
      var timer = setInterval(function () {
        fetchJSON(API_BASE + "/status").then(function (data) {
          setStatus(data);
          if (data.status !== "building") clearInterval(timer);
        }).catch(function () {});
      }, 2000);
      return function () { clearInterval(timer); };
    }, []);

    return status;
  }

  // -- Graph page component -------------------------------------------------

  function GraphPage() {
    var graphData = useState(null);
    var nodes = graphData[0];
    var setNodes = graphData[1];

    var edgesData = useState(null);
    var links = edgesData[0];
    var setLinks = edgesData[1];

    var selectedNode = useState(null);
    var selected = selectedNode[0];
    var setSelected = selectedNode[1];

    var searchQuery = useState("");
    var search = searchQuery[0];
    var setSearch = searchQuery[1];

    var pathFrom = useState("");
    var pFrom = pathFrom[0];
    var setPFrom = pathFrom[1];

    var pathTo = useState("");
    var pTo = pathTo[0];
    var setPTo = pathTo[1];

    var pathResult = useState(null);
    var path = pathResult[0];
    var setPath = pathResult[1];

    var visError = useState(false);
    var visFailed = visError[0];
    var setVisFailed = visError[1];

    // Build mode selection
    var buildMode = useState("ast");
    var bMode = buildMode[0];
    var setBMode = buildMode[1];

    var backendState = useState("ollama");
    var backend = backendState[0];
    var setBackend = backendState[1];

    var modelState = useState("");
    var modelName = modelState[0];
    var setModelName = modelState[1];

    var showSettings = useState(false);
    var settingsOpen = showSettings[0];
    var setSettingsOpen = showSettings[1];

    var status = useGraphStatus();
    var containerRef = useRef(null);
    var networkRef = useRef(null);
    var autoBuildTriggered = useRef(false);

    // Load graph data
    var loadGraph = useCallback(function () {
      fetchJSON(API_BASE + "/graph.json?limit=500").then(function (data) {
        setNodes(data.nodes || []);
        setLinks(data.links || []);
      }).catch(function () {});
    }, []);

    // Auto-build when status is "none" (first visit, no graph yet)
    useEffect(function () {
      if (status.status === "none" && !autoBuildTriggered.current) {
        autoBuildTriggered.current = true;
        fetchJSON(API_BASE + "/build", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "ast" })
        }).catch(function () {});
      }
    }, [status.status]);

    // Load graph when ready
    useEffect(function () {
      if (status.status === "ready" || status.graph_exists) {
        loadGraph();
      }
    }, [status.status, status.graph_exists]);

    // Initialize vis-network when nodes/links change
    useEffect(function () {
      if (!containerRef.current || !nodes || nodes.length === 0) return;

      function renderNetwork() {
        var colorPalette = [
          "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
          "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
          "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000",
          "#aaffc3", "#808000", "#ffd8b1", "#000075", "#808080"
        ];

        var visNodes = nodes.map(function (n) {
          var comm = n.community || n.cluster || 0;
          var color = colorPalette[comm % colorPalette.length];
          var degree = n.degree_centrality || n.degree || 1;
          var size = 10 + Math.min(degree * 5, 40);
          return {
            id: n.id,
            label: n.label || n.id,
            title: (n.type || "node") + ": " + (n.file || ""),
            color: { background: color, border: color },
            size: size,
            font: { color: "#cccccc", size: 12 },
          };
        });

        var visEdges = (links || []).map(function (e) {
          return {
            from: e.source,
            to: e.target,
            arrows: "to",
            label: e.kind || "",
            color: { color: "#444444", highlight: "#ff6b6b" },
            font: { color: "#666666", size: 10 },
          };
        });

        var dataSet = {
          nodes: new vis.DataSet(visNodes),
          edges: new vis.DataSet(visEdges),
        };

        var options = {
          layout: { improvedLayout: true },
          physics: {
            barnesHut: {
              gravitationalConstant: -2000,
              centralGravity: 0.1,
              springLength: 150,
              springConstant: 0.05,
              damping: 0.4,
            },
          },
          interaction: { hover: true, tooltipDelay: 200 },
          nodes: { shape: "dot", borderWidth: 2 },
          edges: { smooth: { type: "continuous" }, width: 1 },
        };

        if (networkRef.current) {
          networkRef.current.destroy();
        }

        networkRef.current = new vis.Network(containerRef.current, dataSet, options);

        networkRef.current.on("click", function (params) {
          if (params.nodes.length > 0) {
            var nodeId = params.nodes[0];
            var node = nodes.find(function (n) { return n.id === nodeId; });
            setSelected(node);
          } else {
            setSelected(null);
          }
        });
      }

      // Check if vis-network is already loaded
      if (typeof vis !== "undefined" && vis.Network) {
        renderNetwork();
      } else {
        var script = document.createElement("script");
        script.src = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js";
        script.onload = function () {
          if (typeof vis === "undefined" || !vis.Network) {
            setVisFailed(true);
            return;
          }
          renderNetwork();
        };
        script.onerror = function () {
          setVisFailed(true);
        };
        document.head.appendChild(script);
      }

      return function () {
        if (networkRef.current) {
          networkRef.current.destroy();
          networkRef.current = null;
        }
      };
    }, [nodes, links]);

    // -- Actions -------------------------------------------------------------

    function handleBuild() {
      var payload = { mode: bMode };
      if (bMode === "semantic") {
        payload.backend = backend;
        if (modelName) payload.model = modelName;
      }
      fetchJSON(API_BASE + "/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).catch(function () {});
    }

    function handleSearch() {
      if (!search) return;
      fetchJSON(API_BASE + "/graph.json?node=" + encodeURIComponent(search)).then(function (data) {
        setNodes(data.nodes || []);
        setLinks(data.links || []);
      });
    }

    function handlePathFind() {
      if (!pFrom || !pTo) return;
      fetchJSON(API_BASE + "/path?from=" + encodeURIComponent(pFrom) + "&to=" + encodeURIComponent(pTo)).then(function (data) {
        setPath(data);
      });
    }

    // -- Render --------------------------------------------------------------

    var statusColor = {
      loading: "#6b7280",
      building: "#f59e0b",
      ready: "#22c55e",
      stale: "#f59e0b",
      error: "#ef4444",
      none: "#6b7280",
      no_code: "#6b7280",
    };

    var isBuilding = status.status === "building";
    var isReady = status.status === "ready" || status.graph_exists;
    var isError = status.status === "error";
    var isEmpty = status.status === "none" || status.status === "no_code" || status.status === "loading";
    var hasNodes = nodes && nodes.length > 0;

    return React.createElement("div", { className: "flex flex-col h-full gap-3 p-4" },
      // Header
      React.createElement("div", { className: "flex items-center justify-between" },
        React.createElement("h2", { className: "text-lg font-bold tracking-wide" }, "Code Graph"),
        React.createElement("div", { className: "flex items-center gap-2" },
          React.createElement("span", {
            className: "inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold text-white",
            style: { backgroundColor: statusColor[status.status] || "#6b7280" }
          }, status.status || "none"),
          React.createElement(Button, { onClick: function() { setSettingsOpen(!settingsOpen); }, size: "sm" }, "\u2699"),
          React.createElement(Button, { onClick: handleBuild, size: "sm" }, "Rebuild")
        )
      ),

      // Build settings panel (collapsible)
      settingsOpen && React.createElement("div", {
        className: "rounded-lg border p-3 flex items-center gap-3 flex-wrap text-xs",
        style: { borderColor: "rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.03)" }
      },
        // Mode toggle
        React.createElement("div", { className: "flex items-center gap-2" },
          React.createElement("span", { style: { color: "#999" } }, "Mode:"),
          React.createElement("label", { className: "flex items-center gap-1", style: { cursor: "pointer" } },
            React.createElement("input", {
              type: "radio",
              checked: bMode === "ast",
              onChange: function() { setBMode("ast"); },
              style: { accentColor: "#22c55e" }
            }),
            React.createElement("span", null, "AST only (fast, local)")
          ),
          React.createElement("label", { className: "flex items-center gap-1", style: { cursor: "pointer" } },
            React.createElement("input", {
              type: "radio",
              checked: bMode === "semantic",
              onChange: function() { setBMode("semantic"); },
              style: { accentColor: "#22c55e" }
            }),
            React.createElement("span", null, "AST + LLM (deeper)")
          )
        ),

        // Backend selector (semantic mode only)
        bMode === "semantic" && React.createElement(React.Fragment, null,
          React.createElement("span", { style: { color: "#666" } }, "|"),
          React.createElement("div", { className: "flex items-center gap-1" },
            React.createElement("span", { style: { color: "#999" } }, "Backend:"),
            React.createElement("select", {
              value: backend,
              onChange: function(e) { setBackend(e.target.value); },
              style: {
                background: "rgba(0,0,0,0.4)", color: "#ccc",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "4px", padding: "2px 6px", fontSize: "12px"
              }
            },
              ["ollama", "openai", "claude", "deepseek", "gemini", "kimi"].map(function(b) {
                return React.createElement("option", { key: b, value: b }, b);
              })
            )
          ),
          React.createElement("div", { className: "flex items-center gap-1" },
            React.createElement("span", { style: { color: "#999" } }, "Model:"),
            React.createElement(Input, {
              placeholder: "default",
              value: modelName,
              onChange: function(e) { setModelName(e.target.value); },
              className: "w-32"
            })
          ),
          backend === "ollama" && React.createElement("span", {
            className: "text-xs",
            style: { color: "#666" }
          }, "Make sure Ollama is running (ollama serve)")
        )
      ),

      // Status bar
      React.createElement("div", { className: "flex items-center gap-4 text-xs", style: { color: "#999" } },
        React.createElement("span", null, (status.node_count || 0) + " nodes"),
        React.createElement("span", null, (status.edge_count || 0) + " edges"),
        React.createElement("span", null, (status.community_count || 0) + " communities"),
        status.mode && status.mode !== "ast" && React.createElement("span", {
          style: { color: "#a78bfa" }
        }, status.mode + (status.backend ? "/" + status.backend : "") + (status.model ? "/" + status.model : "")),
        isBuilding && React.createElement("span", { style: { color: "#f59e0b" } },
          "Building... " + Math.round(status.progress || 0) + "%"
        ),
        isError && React.createElement("span", { style: { color: "#ef4444" } }, status.error)
      ),

      // Toolbar (only when graph exists)
      isReady && React.createElement("div", { className: "flex items-center gap-2 flex-wrap" },
        React.createElement(Input, {
          placeholder: "Search node...",
          value: search,
          onChange: function (e) { setSearch(e.target.value); },
          className: "w-48",
          onKeyDown: function (e) { if (e.key === "Enter") handleSearch(); }
        }),
        React.createElement(Button, { onClick: handleSearch, size: "sm" }, "Search"),
        React.createElement("span", { style: { color: "#666" } }, "|"),
        React.createElement(Input, {
          placeholder: "from",
          value: pFrom,
          onChange: function (e) { setPFrom(e.target.value); },
          className: "w-32"
        }),
        React.createElement("span", null, "\u2192"),
        React.createElement(Input, {
          placeholder: "to",
          value: pTo,
          onChange: function (e) { setPTo(e.target.value); },
          className: "w-32"
        }),
        React.createElement(Button, { onClick: handlePathFind, size: "sm" }, "Find Path")
      ),

      // Main content: graph canvas + inspector
      React.createElement("div", { className: "flex flex-1 min-h-0 gap-3" },
        // Graph canvas with state overlays
        React.createElement("div", {
          ref: containerRef,
          className: "flex-1 min-h-0 rounded-lg border",
          style: {
            minHeight: "300px",
            borderColor: "rgba(255,255,255,0.1)",
            background: "rgba(0,0,0,0.4)",
            position: "relative",
          }
        },
          // Building overlay
          isBuilding && !hasNodes && React.createElement("div", {
            className: "absolute inset-0 flex items-center justify-center",
            style: { color: "#999" }
          },
            React.createElement("div", { className: "text-center" },
              React.createElement("div", { className: "text-lg mb-2" }, "\u2699\uFE0F Building graph..."),
              React.createElement("div", { className: "text-sm" }, "Analyzing code dependencies"),
              React.createElement("div", { className: "mt-2 text-xs", style: { color: "#666" } },
                "This may take a few seconds"
              )
            )
          ),

          // Empty state overlay
          isEmpty && !isBuilding && React.createElement("div", {
            className: "absolute inset-0 flex items-center justify-center",
            style: { color: "#999" }
          },
            React.createElement("div", { className: "text-center" },
              React.createElement("div", { className: "text-lg mb-2" }, "\u{1F50D} No graph yet"),
              React.createElement("div", { className: "text-sm mb-3" },
                status.status === "no_code"
                  ? "No code files detected in working directory"
                  : "Click build to analyze code dependencies"
              ),
              React.createElement(Button, { onClick: handleBuild, size: "sm" }, "Build Graph")
            )
          ),

          // Error overlay
          isError && React.createElement("div", {
            className: "absolute inset-0 flex items-center justify-center",
            style: { color: "#ef4444" }
          },
            React.createElement("div", { className: "text-center" },
              React.createElement("div", { className: "text-lg mb-2" }, "\u26A0\uFE0F Build Error"),
              React.createElement("div", { className: "text-sm", style: { color: "#999" } },
                status.error || "Unknown error"
              ),
              React.createElement("div", { className: "mt-3" },
                React.createElement(Button, { onClick: handleBuild, size: "sm" }, "Retry")
              )
            )
          ),

          // vis-network load failure
          visFailed && hasNodes && React.createElement("div", {
            className: "absolute inset-0 flex items-center justify-center",
            style: { color: "#999" }
          },
            React.createElement("div", { className: "text-center" },
              React.createElement("div", { className: "text-sm" }, "Failed to load vis-network library"),
              React.createElement("div", { className: "text-xs mt-1", style: { color: "#666" } },
                "Check internet connection"
              )
            )
          ),

          // Node count badge
          hasNodes && !isBuilding && React.createElement("div", {
            className: "absolute bottom-2 left-2 text-xs",
            style: { color: "#666" }
          }, nodes.length + " nodes, " + (links || []).length + " edges")
        ),

        // Node inspector / God nodes panel
        React.createElement("div", { className: "w-64 shrink-0 overflow-y-auto" },
          selected ? React.createElement(NodeInspector, {
            node: selected,
            links: links,
            onClose: function () { setSelected(null); }
          }) : React.createElement(GodNodesPanel, { nodes: nodes })
        )
      ),

      // Path result
      path && React.createElement("div", {
        className: "text-xs border p-2 rounded",
        style: { borderColor: "rgba(255,255,255,0.1)" }
      },
        React.createElement("strong", null, "Path: "),
        Array.isArray(path.path) ? path.path.join(" \u2192 ") : JSON.stringify(path)
      )
    );
  }

  // -- Node inspector -------------------------------------------------------

  function NodeInspector(props) {
    var node = props.node;
    var links = props.links;
    var onClose = props.onClose;

    var incoming = (links || []).filter(function (e) { return e.target === node.id; });
    var outgoing = (links || []).filter(function (e) { return e.source === node.id; });

    return React.createElement("div", {
      className: "rounded-lg border p-3",
      style: { borderColor: "rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.03)" }
    },
      React.createElement("div", {
        className: "flex items-center justify-between mb-2 pb-2 border-b",
        style: { borderColor: "rgba(255,255,255,0.1)" }
      },
        React.createElement("span", { className: "text-sm font-semibold" }, node.label || node.id),
        React.createElement("button", {
          onClick: onClose,
          className: "text-sm",
          style: { color: "#999", cursor: "pointer", background: "none", border: "none" }
        }, "\u00D7")
      ),
      React.createElement("div", { className: "space-y-1.5 text-xs", style: { color: "#ccc" } },
        React.createElement("div", null,
          React.createElement("strong", null, "Type: "), node.type || "unknown"
        ),
        node.file && React.createElement("div", null,
          React.createElement("strong", null, "File: "), node.file
        ),
        node.degree_centrality != null && React.createElement("div", null,
          React.createElement("strong", null, "Degree: "), node.degree_centrality.toFixed(3)
        ),
        node.betweenness_centrality != null && React.createElement("div", null,
          React.createElement("strong", null, "Betweenness: "), node.betweenness_centrality.toFixed(3)
        ),
        React.createElement("div", { className: "pt-2" },
          React.createElement("strong", null, "Incoming (" + incoming.length + ")")
        ),
        React.createElement("div", { className: "pl-2", style: { color: "#888" } },
          incoming.map(function (e, i) {
            return React.createElement("div", { key: "in" + i }, e.source + " (" + (e.kind || "") + ")");
          })
        ),
        React.createElement("div", { className: "pt-2" },
          React.createElement("strong", null, "Outgoing (" + outgoing.length + ")")
        ),
        React.createElement("div", { className: "pl-2", style: { color: "#888" } },
          outgoing.map(function (e, i) {
            return React.createElement("div", { key: "out" + i }, e.target + " (" + (e.kind || "") + ")");
          })
        )
      )
    );
  }

  // -- God nodes panel ------------------------------------------------------

  function GodNodesPanel(props) {
    var nodes = props.nodes || [];

    var sorted = nodes.slice().sort(function (a, b) {
      var da = a.degree_centrality || a.degree || 0;
      var db = b.degree_centrality || b.degree || 0;
      return db - da;
    }).slice(0, 10);

    return React.createElement("div", {
      className: "rounded-lg border p-3",
      style: { borderColor: "rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.03)" }
    },
      React.createElement("div", {
        className: "text-sm font-semibold mb-2 pb-2 border-b",
        style: { borderColor: "rgba(255,255,255,0.1)" }
      }, "Top Nodes"),
      React.createElement("div", { className: "space-y-1 text-xs", style: { color: "#ccc" } },
        sorted.map(function (n, i) {
          var deg = n.degree_centrality || n.degree || 0;
          return React.createElement("div", {
            key: n.id,
            className: "flex justify-between items-center py-0.5",
            style: { cursor: "pointer" },
          },
            React.createElement("span", { className: "truncate" }, (i + 1) + ". " + (n.label || n.id)),
            React.createElement("span", { className: "shrink-0 ml-2", style: { color: "#666" } }, deg.toFixed(2))
          );
        }),
        sorted.length === 0 && React.createElement("div", { style: { color: "#666" } }, "No nodes yet")
      )
    );
  }

  // -- Register -------------------------------------------------------------

  window.__HERMES_PLUGINS__.register("graphify", GraphPage);
})();
