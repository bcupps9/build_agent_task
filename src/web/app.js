(() => {
  const e = React.createElement;
  const { useEffect, useMemo, useState } = React;

  // ---------- tiny on-page toast for errors/info ----------
  const showMsg = (msg, isErr = true) => {
    try {
      const el = document.createElement('div');
      el.style.cssText =
        'position:fixed;bottom:12px;left:12px;right:12px;background:#1b233a;color:' +
        (isErr ? '#ffb4b4' : '#b4ffd2') +
        ';border:1px solid ' +
        (isErr ? '#5a2530' : '#295a3a') +
        ';padding:8px;border-radius:8px;font:12px/1.4 monospace;z-index:9999;';
      el.textContent = (isErr ? '[UI Error] ' : '[UI] ') + msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 8000);
    } catch {}
  };
  window.addEventListener('error', (ev) => showMsg(ev?.error?.message || ev?.message || 'Unknown error'));
  window.addEventListener('unhandledrejection', (ev) => showMsg(ev?.reason?.message || String(ev?.reason)));

  // ---------- React Flow UMD detection ----------
  const RF = window.ReactFlow;
  const ReactFlowCmp = RF?.ReactFlow || RF?.default || RF;
  if (!ReactFlowCmp) showMsg('ReactFlow failed to load. Check index.html script tags.');

  // ---------- Dagre auto-layout (top → bottom) ----------
  function layoutWithDagre(dagJson) {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
    g.setDefaultEdgeLabel(() => ({}));

    const NODE_W = 230, NODE_H = 120;

    (dagJson.nodes || []).forEach((n) => {
      g.setNode(n.id, { width: NODE_W, height: NODE_H, label: n.label, tasks: n.tasks || [] });
    });
    (dagJson.edges || []).forEach(([s, t]) => g.setEdge(s, t));

    dagre.layout(g);

    const nodes = (dagJson.nodes || []).map((n) => {
      const p = g.node(n.id);
      return {
        id: n.id,
        position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
        data: { label: n.label, tasks: n.tasks || [], status: 'idle' },
        type: 'default',
      };
    });

    const edges = (dagJson.edges || []).map(([s, t], i) => ({
      id: `${s}-${t}-${i}`,
      source: s,
      target: t,
      type: 'smoothstep',
      animated: false,
    }));

    return { nodes, edges };
  }

  // ---------- Node renderer with status badge ----------
  function DagCard({ nodes, edges }) {
    const nodeTypes = useMemo(() => ({
      default: ({ data }) => {
        const color =
          data.status === 'running' ? '#f0ad4e' :
          data.status === 'done'    ? '#28a745' :
                                      '#6c757d';
        return e('div', { className: 'card', style: { width: 230, padding: 10, borderColor: color } },
          e('div', { className: 'hdr', style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
            e('h2', null, data.label || 'Node'),
            e('span', { className: 'badge', style: { background: color } }, data.status || 'idle')
          ),
          !!data.tasks && e('div', { className: 'small' }, 'Tasks'),
          !!data.tasks && e('ul', { className: 'small' }, data.tasks.map((t, i) => e('li', { key: i }, '• ', t)))
        );
      },
    }), []);

    return e('div', { className: 'card rf-card' },
      e('div', { className: 'hdr' }, e('h2', null, 'DAG Visualization')),
      ReactFlowCmp
        ? e(ReactFlowCmp, { nodes, edges, nodeTypes, fitView: true })
        : e('div', { className: 'small' }, 'ReactFlow missing; see error strip.')
    );
  }

  // ---------- Control Panel ----------
  function ControlCard({ onStarted }) {
    const [prompt, setPrompt] = useState('Find industrial sites near Phoenix, AZ');
    const [location, setLocation] = useState('Phoenix, AZ');
    const [maxCand, setMaxCand] = useState(8);
    const [running, setRunning] = useState(false);

    const doRun = async () => {
      try {
        setRunning(true);
        const res = await fetch('/api/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, location, max_candidates: Number(maxCand) })
        });
        if (!res.ok) {
          const txt = await res.text().catch(() => '');
          throw new Error(`POST /api/execute ${res.status} ${txt}`);
        }
        showMsg('✔ queued run', false);
        onStarted && onStarted();
      } catch (err) {
        showMsg(err?.message || String(err));
      } finally {
        setRunning(false);
      }
    };

    return e('div', { className: 'card' },
      e('div', { className: 'hdr' },
        e('h2', null, 'Control Panel'),
        e('button', { type: 'button', className: 'btn', onClick: doRun, disabled: running }, 'Execute')
      ),
      e('div', { className: 'grid2' },
        e('div', null,
          e('div', { className: 'small' }, 'Prompt'),
          e('textarea', { rows: 4, value: prompt, onChange: (ev) => setPrompt(ev.target.value) })
        ),
        e('div', null,
          e('div', { className: 'small' }, 'Location'),
          e('input', { value: location, onChange: (ev) => setLocation(ev.target.value) }),
          e('div', { className: 'small', style: { marginTop: 8 } }, 'Max candidates'),
          e('input', { type: 'number', min: 1, max: 20, value: maxCand, onChange: (ev) => setMaxCand(ev.target.value) })
        )
      )
    );
  }

  // ---------- Badges row ----------
  function Badges({ status }) {
    const order = ['input_parser', 'ideation', 'zoning_ranker', 'infra_ranker', 'labor_ranker', 'report'];
    return e('div', { className: 'card' },
      e('div', { className: 'hdr' }, e('h2', null, 'Execution Status')),
      e('div', { className: 'flex' },
        order.map((n) => {
          const s = status[n] || 'idle';
          const cls = s === 'running' ? 'badge run' : (s === 'done' ? 'badge done' : 'badge idle');
          return e('div', { key: n, className: cls }, n);
        })
      )
    );
  }

  // ---------- App (fetch DAG, WS stream, color nodes, animate edges) ----------
  function App() {
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);
    const [status, setStatus] = useState({});
    const [report, setReport] = useState('');

    // fetch DAG once
    useEffect(() => {
      fetch('/api/dag')
        .then((r) => { if (!r.ok) throw new Error('GET /api/dag ' + r.status); return r.json(); })
        .then((d) => {
          const laid = layoutWithDagre(d);
          setNodes(laid.nodes);
          setEdges(laid.edges);
        })
        .catch((err) => showMsg(err?.message || String(err)));
    }, []);

    // single WS for everything
    useEffect(() => {
      const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      const ws = new WebSocket(proto + location.host + '/ws');
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'run_start') {
            setStatus({});
            setReport('');
            setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, status: 'idle' } })));
            setEdges((es) => es.map((ed) => ({ ...ed, animated: false })));
          }
          if (msg.type === 'node_start') {
            setStatus((s) => ({ ...s, [msg.node]: 'running' }));
            setNodes((ns) => ns.map((n) => (n.id === msg.node ? { ...n, data: { ...n.data, status: 'running' } } : n)));
            setEdges((es) => es.map((ed) => (ed.source === msg.node ? { ...ed, animated: true } : ed)));
          }
          if (msg.type === 'node_end') {
            setStatus((s) => ({ ...s, [msg.node]: 'done' }));
            setNodes((ns) => ns.map((n) => (n.id === msg.node ? { ...n, data: { ...n.data, status: 'done' } } : n)));
            setEdges((es) => es.map((ed) => (ed.source === msg.node ? { ...ed, animated: false } : ed)));
          }
          if (msg.type === 'result') setReport(msg.report_md || '');
          if (msg.type === 'run_error') {
            setReport('ERROR: ' + (msg.error || 'unknown'));
            showMsg(msg.error || 'run error');
          }
        } catch (e) {
          showMsg('WS parse: ' + (e?.message || String(e)));
        }
      };
      ws.onerror = () => showMsg('WebSocket error');
      return () => { try { ws.close(); } catch {} };
    }, []);

    return e(React.Fragment, null,
      e('div', { className: 'card', style: { gridColumn: '1 / span 1' } },
        e('div', { className: 'hdr' }, e('h2', null, 'Milestone 2 — Site Sourcing Agent')),
        e('div', { className: 'small' }, 'Visualize the DAG, run, and monitor progress.')
      ),
      e(DagCard, { nodes, edges }),
      e('div', null,
        e(ControlCard, { onStarted: () => {} }),
        e(Badges, { status }),
        e('div', { className: 'card' },
          e('div', { className: 'hdr' }, e('h2', null, 'Results')),
          report ? e('pre', null, report) : e('div', { className: 'small' }, 'Run the workflow to see results...')
        )
      ),
      e('div', { className: 'card' },
        e('div', { className: 'hdr' }, e('h2', null, 'Boot Check')),
        e('div', { className: 'small' },
          `React: ${!!window.React}, ReactDOM: ${!!window.ReactDOM}, ReactFlow: ${!!window.ReactFlow}`
        )
      )
    );
  }

  ReactDOM.createRoot(document.getElementById('app')).render(e(App));
})();
