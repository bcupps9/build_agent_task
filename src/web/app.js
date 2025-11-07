(() => {
  const e = React.createElement;

  // ---------- tiny on-page toast ----------
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

  // ---------- React Flow ----------
  const RF = window.ReactFlow;
  const ReactFlowCmp = RF?.ReactFlow || RF?.default || RF;
  if (!ReactFlowCmp) showMsg('ReactFlow failed to load. Check index.html script tags.');

  // ---------- Dagre layout ----------
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

  // ---------- Node renderer ----------
  function DagCard({ nodes, edges }) {
    const nodeTypes = React.useMemo(() => ({
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

  function DesignerCard({ onSpec }) {
    const [desc, setDesc] = React.useState(
`Goal: Site selection for industrial hub near a given city.
Nodes:
- Parse user inputs and city
- Ideate candidate areas
- Rank by zoning, labor, infrastructure (in parallel)
- Draft final report`
    );
    const [jsonSpec, setJsonSpec] = React.useState('');

    const doGenerate = async () => {
      try {
        const res = await fetch('/api/generate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: desc })
        });
        if (!res.ok) throw new Error('generate ' + res.status);
        const spec = await res.json();
        setJsonSpec(JSON.stringify(spec, null, 2));
        onSpec && onSpec(spec);
        showMsg('✔ spec generated', false);
      } catch (e) { showMsg(e?.message || String(e)); }
    };

    return e('div', { className:'card' },
      e('div', { className:'hdr' }, e('h2', null, 'Design from Description'),
        e('button', { className:'btn', onClick: doGenerate, style:{marginLeft:'auto'} }, 'Generate')
      ),
      e('div', { className:'grid2' },
        e('div', null,
          e('div', { className:'small' }, 'Description'),
          e('textarea', { rows: 8, value: desc, onChange: ev => setDesc(ev.target.value) })
        ),
        e('div', null,
          e('div', { className:'small' }, 'Generated Spec (read-only)'),
          e('textarea', { rows: 8, value: jsonSpec, readOnly: true })
        )
      )
    );
  }

  function ControlCardGenerated({ specRef, onStarted }) {
    const [prompt, setPrompt] = React.useState('Run with my inputs');
    const [running, setRunning] = React.useState(false);

    const doRun = async () => {
      try {
        setRunning(true);
        const res = await fetch('/api/execute_generated', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, spec: specRef.current })
        });
        if (!res.ok) throw new Error('execute_generated ' + res.status);
        onStarted && onStarted();
        showMsg('✔ queued run', false);
      } catch (e) { showMsg(e?.message || String(e)); }
      finally { setRunning(false); }
    };

    return e('div', { className:'card' },
      e('div', { className:'hdr' },
        e('h2', null, 'Control Panel'),
        e('button', { className:'btn', onClick: doRun, disabled: !specRef.current || running }, 'Execute')
      ),
      e('div', { className:'small' },
        specRef.current ? 'Ready to run generated workflow.' : 'Generate a workflow first in the designer above.'
      ),
      e('div', null,
        e('div', { className:'small', style:{marginTop:8} }, 'Prompt'),
        e('textarea', { rows: 3, value: prompt, onChange: ev => setPrompt(ev.target.value) })
      )
    );
  }

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

  function App() {
    const [nodes, setNodes] = React.useState([]);
    const [edges, setEdges] = React.useState([]);
    const [status, setStatus] = React.useState({});
    const [report, setReport] = React.useState('');

    // holds the latest generated WorkflowSpec from /api/generate
    const specRef = React.useRef(null);

    const onSpec = (spec) => {
      specRef.current = spec;
      const laid = layoutWithDagre(spec);
      setNodes(laid.nodes);
      setEdges(laid.edges);
    };

    React.useEffect(() => {
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

          // static path events
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

          // dynamic path pulse
          if (msg.type === 'node_tick') {
            const nodeId = msg.node;
            if (nodeId) {
              setStatus((s) => ({ ...s, [nodeId]: 'running' }));
              setNodes((ns) => ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, status: 'running' } } : n)));
              setEdges((es) => es.map((ed) => (ed.source === nodeId ? { ...ed, animated: true } : ed)));

              // optimistic finalize after a brief delay
              setTimeout(() => {
                setStatus((s) => ({ ...s, [nodeId]: 'done' }));
                setNodes((ns) => ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, status: 'done' } } : n)));
                setEdges((es) => es.map((ed) => (ed.source === nodeId ? { ...ed, animated: false } : ed)));
              }, 250);
            }
          }

          if (msg.type === 'result') setReport(msg.report_md || '');
          if (msg.type === 'result_final') setReport(msg.report_md || '');

          if (msg.type === 'run_end') {
            // finalize any lingering animations
            setNodes((ns) => ns.map((n) =>
              n.data.status === 'running' ? { ...n, data: { ...n.data, status: 'done' } } : n
            ));
            setEdges((es) => es.map((ed) => ({ ...ed, animated: false })));
          }

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

    return React.createElement(React.Fragment, null,
      e('div', { className: 'card', style: { gridColumn: '1 / span 1' } },
        e('div', { className: 'hdr' }, e('h2', null, 'Milestone 3 — Generated Workflows')),
        e('div', { className: 'small' }, 'Describe a workflow, generate a DAG, then execute it.')
      ),
      e(DesignerCard, { onSpec }),
      e(DagCard, { nodes, edges }),
      e(ControlCardGenerated, { specRef, onStarted: () => {} }),
      e(Badges, { status }),
      e('div', { className: 'card' },
        e('div', { className: 'hdr' }, e('h2', null, 'Results')),
        report ? e('pre', null, report)
               : e('div', { className: 'small' }, 'Generate a workflow and run it to see results...')
      ),
      e('div', { className: 'card' },
        e('div', { className: 'hdr' }, e('h2', null, 'Boot Check')),
        e('div', { className: 'small' },
          `React: ${!!window.React}, ReactDOM: ${!!window.ReactDOM}, ReactFlow: ${!!window.ReactFlow}`)
      )
    );
  }

  ReactDOM.createRoot(document.getElementById('app')).render(e(App));
})();
