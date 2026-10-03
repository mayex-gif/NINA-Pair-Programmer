import React, { useEffect, useState, useCallback, useRef } from 'react';
import ReactFlow, {
  MiniMap,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';

// ---------------------------------------------------------------------------
// Resolución de imports -> archivos del mapa  (función pura, sin React)
// ---------------------------------------------------------------------------
// <resolver>
const EXT_JS = ['.jsx', '.js', '.tsx', '.ts', '.mjs'];
const quitarExt = (p) => p.replace(/\.(jsx?|tsx?|mjs|java|py)$/, '');
const dirname = (p) => p.split('/').slice(0, -1).join('/');

// Normaliza 'a/b/../c/./d' -> 'a/c/d'
const normalizar = (ruta) => {
  const out = [];
  for (const seg of ruta.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') out.pop();
    else out.push(seg);
  }
  return out.join('/');
};

// Índice de sufijos: 'a/b/C.java' -> 'a/b/C', 'b/C', 'C'  (cada uno apunta a los paths que lo cumplen)
const indexarSufijos = (paths, transformar) => {
  const idx = new Map();
  for (const p of paths) {
    const partes = transformar(p).split('/');
    for (let i = 0; i < partes.length; i++) {
      const suf = partes.slice(i).join('/');
      if (!idx.has(suf)) idx.set(suf, []);
      idx.get(suf).push(p);
    }
  }
  return idx;
};

// Ante varios candidatos: el que comparte más carpetas con el importador; si empatan, el más corto
const elegir = (candidatos, desde) => {
  if (!candidatos || candidatos.length === 0) return null;
  if (candidatos.length === 1) return candidatos[0];
  const a = desde.split('/');
  const comunes = (p) => {
    const b = p.split('/');
    let n = 0;
    while (n < a.length && n < b.length && a[n] === b[n]) n++;
    return n;
  };
  return [...candidatos].sort((x, y) => comunes(y) - comunes(x) || x.length - y.length)[0];
};

export function resolverImports(files) {
  const paths = Object.keys(files);
  const set = new Set(paths);
  const idxJava = indexarSufijos(paths.filter((p) => p.endsWith('.java')), quitarExt);
  const idxPy = indexarSufijos(
    paths.filter((p) => p.endsWith('.py')),
    (p) => quitarExt(p).replace(/\/__init__$/, '')
  );
  const idxJs = indexarSufijos(
    paths.filter((p) => /\.(jsx?|tsx?|mjs)$/.test(p)),
    (p) => quitarExt(p).replace(/\/index$/, '')
  );

  const probar = (base) => {
    if (set.has(base)) return base;
    for (const e of EXT_JS) if (set.has(base + e)) return base + e;
    for (const e of EXT_JS) if (set.has(`${base}/index${e}`)) return `${base}/index${e}`;
    return null;
  };

  // com.lwt.backend.service.UserService  (o import static ...UserService.metodo)
  const resolverJava = (imp, desde) => {
    const partes = imp.split('.');
    while (partes.length > 0) {
      const hit = elegir(idxJava.get(partes.join('/')), desde);
      if (hit) return hit;
      partes.pop();
    }
    return null;
  };

  // 'app.services.user' | '.models' | '..utils.helpers'  (el mapa guarda también 'modulo.nombre')
  const resolverPy = (imp, desde) => {
    const m = imp.match(/^(\.+)(.*)$/);
    if (m) {
      let dir = dirname(desde);
      for (let i = 1; i < m[1].length; i++) dir = dirname(dir);
      const partes = m[2] ? m[2].split('.') : [];
      for (;;) {
        const base = normalizar([dir, ...partes].join('/'));
        for (const c of [`${base}.py`, `${base}/__init__.py`]) if (set.has(c)) return c;
        if (partes.length === 0) return null;
        partes.pop();
      }
    }
    const partes = imp.split('.');
    while (partes.length > 0) {
      const hit = elegir(idxPy.get(partes.join('/')), desde);
      if (hit) return hit;
      partes.pop();
    }
    return null;
  };

  // <script src="app.js"> en un .html: relativo a su carpeta
  const resolverHtml = (imp, desde) => {
    if (/^(https?:)?\/\//.test(imp)) return null;
    return probar(normalizar(`${dirname(desde)}/${imp}`));
  };

  const resolverJs = (imp, desde) => {
    if (imp.startsWith('.')) {
      return probar(normalizar(`${dirname(desde)}/${imp}`)); // import relativo: resolución exacta
    }
    let rel;
    if (/^[@~]\//.test(imp)) rel = imp.replace(/^[@~]\//, ''); // alias tipo '@/components/Foo'
    else if (imp.startsWith('@')) return null; // paquete npm con scope (@mui/material)
    else if (!imp.includes('/')) return null; // paquete suelto (react, axios)
    else rel = imp; // 'components/Foo' (baseUrl) o 'next/link' (no va a matchear)
    return elegir(idxJs.get(quitarExt(rel)), desde);
  };

  const vistos = new Set();
  const aristas = [];
  for (const [path, data] of Object.entries(files)) {
    const imports = (data && data.imports) || [];
    for (const imp of imports) {
      const destino = path.endsWith('.java') ? resolverJava(imp, path)
        : path.endsWith('.py') ? resolverPy(imp, path)
        : path.endsWith('.html') ? resolverHtml(imp, path)
        : resolverJs(imp, path);
      if (!destino || destino === path) continue;
      const k = `${path}→${destino}`;
      if (vistos.has(k)) continue;
      vistos.add(k);
      aristas.push([path, destino]);
    }
  }
  return aristas;
}
// </resolver>

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------
const ANCHO = 250;
const ALTO = 80;

const getLayoutedElements = (nodes, edges, direction = 'LR') => {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 50, ranksep: 200 }); // LR = horizontal, TB = vertical
  nodes.forEach((n) => g.setNode(n.id, { width: ANCHO, height: ALTO }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return {
    nodes: nodes.map((n) => {
      const p = g.node(n.id);
      return { ...n, position: { x: p.x - ANCHO / 2, y: p.y - ALTO / 2 } };
    }),
    edges,
  };
};

const colorDe = (path) => {
  const raiz = path.split('/')[0];
  if (path.endsWith('.py')) return { bg: '#3b2f0b', borde: '#ca8a04' }; // ámbar
  if (path.endsWith('.html')) return { bg: '#2e1065', borde: '#8b5cf6' }; // violeta
  if (path.endsWith('.java') || raiz === 'backend') return { bg: '#14532d', borde: '#22c55e' }; // verde
  if (raiz === 'frontend') return { bg: '#0f172a', borde: '#3b82f6' }; // azul
  return { bg: '#1f2937', borde: '#374151' }; // gris
};

// ---------------------------------------------------------------------------
// Componente
// refreshMs > 0: vuelve a leer el mapa cada N ms (útil mientras el Vigía lo actualiza).
// Solo re-dibuja si el contenido cambió, para no perder las posiciones que moviste a mano.
// ---------------------------------------------------------------------------
export default function RepomapGraph({ jsonUrl = '/.ai_map.json', refreshMs = 0 }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const ultimoTexto = useRef('');

  const buildGraph = useCallback(
    (data) => {
      const files = data.files || data; // formato v2: { version, files }, o v1 plano
      const filePaths = Object.keys(files);

      const initialNodes = filePaths.map((path) => {
        const partes = path.split('/');
        const fileName = partes[partes.length - 1];
        const carpeta = partes.slice(0, -1).join('/') || '.';
        const info = files[path];
        const nFirmas = info && Array.isArray(info.signatures) ? info.signatures.length : 0;
        const { bg, borde } = colorDe(path);

        return {
          id: path,
          data: {
            label: (
              <div className="flex flex-col items-start p-2">
                <span className="text-xs text-gray-400 font-mono mb-1 truncate w-full" title={carpeta}>
                  {carpeta}/
                </span>
                <span className="font-bold text-white">{fileName}</span>
                <span className="text-[10px] text-gray-500 mt-1">{nFirmas} firmas</span>
              </div>
            ),
          },
          position: { x: 0, y: 0 }, // dagre lo calcula después
          style: {
            background: bg,
            border: `2px solid ${borde}`,
            borderRadius: '8px',
            color: 'white',
            width: ANCHO,
          },
        };
      });

      const initialEdges = resolverImports(files).map(([source, target]) => ({
        id: `e-${source}-${target}`,
        source,
        target,
        animated: true,
        markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
        style: { stroke: '#6b7280' },
      }));

      const { nodes: n, edges: e } = getLayoutedElements(initialNodes, initialEdges, 'LR');
      setNodes(n);
      setEdges(e);
    },
    [setNodes, setEdges]
  );

  const cargar = useCallback(async () => {
    try {
      const res = await fetch(jsonUrl, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status} al pedir ${jsonUrl}`);
      const texto = await res.text();
      if (texto === ultimoTexto.current) return; // sin cambios: no re-layout
      let data;
      try {
        data = JSON.parse(texto);
      } catch {
        throw new Error(`${jsonUrl} no devolvió JSON (¿copiaste .ai_map.json a la carpeta public/?)`);
      }
      ultimoTexto.current = texto;
      buildGraph(data);
      setError(null);
    } catch (err) {
      console.error('Error cargando el Repomap:', err);
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [jsonUrl, buildGraph]);

  useEffect(() => {
    cargar();
    if (refreshMs > 0) {
      const id = setInterval(cargar, refreshMs);
      return () => clearInterval(id);
    }
  }, [cargar, refreshMs]);

  if (loading) {
    return (
      <div className="flex justify-center items-center h-screen text-white">
        Cargando Grafo Arquitectónico...
      </div>
    );
  }

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#030712', position: 'relative' }}>
      {error && (
        <div
          style={{
            position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', zIndex: 10,
            background: '#7f1d1d', color: 'white', padding: '8px 14px', borderRadius: 8, fontSize: 13,
          }}
        >
          ⚠️ {error}
        </div>
      )}
      {!error && nodes.length === 0 && (
        <div className="flex justify-center items-center h-screen text-gray-400">
          El mapa está vacío. Ejecutá generar_mapa.py en tu proyecto.
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
      >
        <Controls />
        <MiniMap nodeColor={(node) => (node.style && node.style.background) || '#1f2937'} />
        <Background color="#1f2937" gap={16} />
      </ReactFlow>
    </div>
  );
}