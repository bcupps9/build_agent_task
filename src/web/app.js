(() => {
  const e = React.createElement;
  const { useEffect, useMemo, useState } = React;

  // ---------- on-page error strip ----------
  const showErr = (msg) => {
    try {
      const el = document.createElement('div');
      el.style.cssText =
        'position:fixed;bottom:12px;left:12px;right:12px;background:#1b233a;color:#ffb4b4;border:1px solid #5a2530;padding:8px;border-radius:8px;font:12px/1.4 monospace;z-index:9999;';
      el.textContent = `[UI Error] ${msg}`;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 10000);
    } catch (_) {}
  };

  // catch any unhandled errors
  window.addEventListener('error', (ev) => showErr(ev?.error?.message || ev?.message || 'Unknown error'));
  window.addEventListener('unhandledrejection', (ev) => showErr(ev?.reason?.message || String(ev?.reason)));

  // ---------- React Flow UMD detection ----------
  const RF = window.ReactFlow; // provided by /dist/umd/index.min.js
  const ReactFlowCmp = RF?.ReactFlow || RF?.default || RF;
  if (!ReactFlowCmp) {
    showErr('ReactFlow failed to load (window.ReactFlow is undefined). Check the <script> tag src and network tab.');
  }

  function DagCard({ nodes, edges }) {
    const nodeTypes = useMemo(() => ({
      default: ({ data }) =>
        e('div', { className: 'card', style: { width: 220, padding: 10 } },
          e('div', { className: 'hdr' }, e('h2', null, data.label || 'Node')),
          !!data.tasks && e('div', { className: 'small' }, 'Tasks'),
          !!data.tasks && e('ul', { className: 'small' }, data.tasks.map((t, i) => e('li', { key: i }, '• ', t)))
        ),
    }), []);

    return e('div', { className: 'card rf-card' },
      e('div', { className: 'hdr' }, e('h2', null, 'DAG Visualization')),
      ReactFlowCmp
        ? e(ReactFlowCmp, { nodes, edges, nodeTypes, fitView: true })
        : e('div', { className: 'small' }, 'ReactFlow missing; see error strip.')
    );
  }

  function ControlCard({ onExecute }) {
    const [prompt, setPrompt] = useState('Find industrial sites near Phoenix, AZ');
    const [location, setLocation] = useState('Phoenix, AZ');
    const [maxCand, setMaxCand] = useState(8);
    const [running, setRunning] = useState(false);

    const doRun = async () => {
      try { console.log('[UI] Execute clicked'); } catch {}
      try {
        setRunning(true);
        const res = await fetch('/api/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, location, max_candidates: Number(maxCand) })
        });
        if (!res.ok) {
          const txt = await res.text().catch(()=> '');
          throw new Error(`POST /api/execute ${res.status} ${txt}`);
        }
        showErr('✔ queued run');               // quick visual confirmation

        onExecute && onExecute();
      } catch (err) {
        showErr(err?.message || String(err));
      } finally {
        setRunning(false);
      }
    };

    return e('div', { className: 'card' },
      e('div', { className: 'hdr' }, e('h2', null, 'Control Panel'),
        e('button', {
            type: 'button',                    // ← ensure not a form submit
            className:'btn',
            onClick: doRun,
            disabled: running
          }, 'Execute')
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
  
  function StatusCard() {
    const [status, setStatus] = useState({});
    useEffect(() => {
      const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      const ws = new WebSocket(proto + location.host + '/ws');
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'node_start') setStatus(s => ({ ...s, [msg.node]: 'running' }));
          if (msg.type === 'node_end') setStatus(s => ({ ...s, [msg.node]: 'done' }));
          if (msg.type === 'run_start') setStatus({});
        } catch (e) {
          showErr('WS parse: ' + (e?.message || String(e)));
        }
      };
      ws.onerror = () => showErr('WebSocket error');
      return () => { try { ws.close(); } catch { } };
    }, []);

    const nodes = ['input_parser', 'ideation', 'zoning_ranker', 'infra_ranker', 'labor_ranker', 'report'];
    return e('div', { className: 'card' },
      e('div', { className: 'hdr' }, e('h2', null, 'Execution Status')),
      e('div', { className: 'flex' },
        nodes.map(n => {
          const s = status[n] || 'idle';
          const cls = s === 'running' ? 'badge run' : (s === 'done' ? 'badge done' : 'badge idle');
          return e('div', { key: n, className: cls }, n);
        })
      )
    );
  }

  function ResultsCard() {
    const [report, setReport] = useState('');
    useEffect(() => {
      const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      const ws = new WebSocket(proto + location.host + '/ws');
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'result') setReport(msg.report_md || '');
          if (msg.type === 'run_error') setReport('ERROR: ' + (msg.error || 'unknown'));
        } catch (e) {
          showErr('WS parse: ' + (e?.message || String(e)));
        }
      };
      return () => { try { ws.close(); } catch { } };
    }, []);
    return e('div', { className: 'card' },
      e('div', { className: 'hdr' }, e('h2', null, 'Results')),
      report ? e('pre', null, report) : e('div', { className: 'small' }, 'Run the workflow to see results...')
    );
  }

  function BootCheck() {
    return e('div', { className: 'card' },
      e('div', { className: 'hdr' }, e('h2', null, 'Boot Check')),
      e('div', { className: 'small' },
        `React: ${!!window.React}, ReactDOM: ${!!window.ReactDOM}, ReactFlow: ${!!window.ReactFlow}`
      )
    );
  }

  function App() {
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);

    // Minimal placeholder DAG so you SEE something even if /api/dag fails
    useEffect(() => {
      // placeholder
      const pNodes = [
        { id: 'a', position: { x: 80, y: 60 }, data: { label: 'Placeholder A', tasks: ['Loading DAG...'] } },
        { id: 'b', position: { x: 320, y: 220 }, data: { label: 'Placeholder B', tasks: ['Loading DAG...'] } },
      ];
      const pEdges = [{ id: 'a-b', source: 'a', target: 'b' }];
      setNodes(pNodes);
      setEdges(pEdges);

      // real DAG
      fetch('/api/dag')
        .then(r => {
          if (!r.ok) throw new Error('GET /api/dag ' + r.status);
          return r.json();
        })
        .then(d => {
          const n = (d.nodes || []).map((node, idx) => ({
            id: node.id,
            position: { x: 80 + 220 * (idx % 3), y: 60 + 160 * Math.floor(idx / 3) },
            data: { label: node.label, tasks: node.tasks || [] }
          }));
          const egs = (d.edges || []).map(([s, t]) => ({ id: s + '-' + t, source: s, target: t }));
          setNodes(n);
          setEdges(egs);
        })
        .catch(err => showErr(err?.message || String(err)));
    }, []);

    return e(React.Fragment, null,
      e('div', { className: 'card', style: { gridColumn: '1 / span 1' } },
        e('div', { className: 'hdr' }, e('h2', null, 'Milestone 2 — Site Sourcing Agent')),
        e('div', { className: 'small' }, 'Visualize the DAG, run, and monitor progress.')
      ),
      e(DagCard, { nodes, edges }),
      e('div', null,
        e(ControlCard, null),
        e(StatusCard, null),
        e(ResultsCard, null),
        e(BootCheck, null)
      )
    );
  }

  ReactDOM.createRoot(document.getElementById('app')).render(e(App));
})();
