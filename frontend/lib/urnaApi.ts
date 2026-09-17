const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Postura = "a_favor" | "en_contra" | "neutro";

export interface UrnaCandidato {
  nombre: string; pct: number; pos: number; neg: number; neu: number; menciones: number;
  // % de cada postura sobre las menciones de ESTE candidato (ej. 20/10/70).
  pos_pct: number; neg_pct: number; neu_pct: number;
  // Desglose de menciones por red (bloques): { twitter, instagram, tiktok }.
  por_red?: Record<string, number>;
}
export interface UrnaBloque { red: string; encontrados: number; analizados: number; }
export interface UrnaPost {
  network: string; author: string; author_url: string; text: string; post_url: string; date: string;
}
export interface UrnaEvidencia {
  candidato: string; postura: Postura; cita: string; post: UrnaPost;
}
export interface UrnaConsultora {
  consultora: string; pct: number; gap: number;
  fuente_url?: string; fuente_titulo?: string; fecha?: string;
}
export interface UrnaComparacion {
  candidato: string; redes_pct: number; consultoras: UrnaConsultora[];
  promedio_consultoras: number | null; gap_promedio: number | null;
}
export interface UrnaMeta {
  total_posts: number; posts_electorales: number; disclaimer: string; warnings: string[];
  analizados?: number;
  bloques?: UrnaBloque[];
}
export interface UrnaResponse {
  candidatos: UrnaCandidato[]; evidencia: UrnaEvidencia[];
  comparacion: UrnaComparacion[]; meta: UrnaMeta;
}
export interface UrnaRequest {
  keywords: string[]; networks: string[]; date: string | null; country: string;
  pollster_csv: string; auto_consultoras: boolean;
}

export interface UrnaProgress { phase: string; pct: number; }
export interface UrnaJobStatus {
  state: "running" | "done" | "error";
  progress: UrnaProgress;
  result: UrnaResponse | null;
  error: string | null;
}

// El análisis corre en SEGUNDO PLANO en el backend (puede tardar ~2-4 min con el
// corpus grande). Por eso el frontend arranca un trabajo y consulta su estado, en
// vez de esperar una sola request larga que chocaría con cualquier timeout.
const START_TIMEOUT_MS = 90_000;   // el /start es rápido, pero puede tener que despertar Render (~40s)
const POLL_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 2_500;
const MAX_WAIT_MS = 10 * 60_000;   // tope de seguridad para no consultar para siempre

async function post(path: string, body: unknown, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function detailOrDefault(res: Response, fallback: string): Promise<string> {
  try { return (await res.json())?.detail || fallback; } catch { return fallback; }
}

export async function startBocaDeUrna(req: UrnaRequest): Promise<string> {
  const res = await post("/api/boca-de-urna/start", req, START_TIMEOUT_MS);
  if (!res.ok) throw new Error(await detailOrDefault(res, `No se pudo iniciar el análisis (${res.status}).`));
  return (await res.json()).job_id as string;
}

async function fetchStatus(jobId: string): Promise<UrnaJobStatus> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), POLL_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}/api/boca-de-urna/status/${jobId}`, { signal: controller.signal });
    if (!res.ok) throw new Error(await detailOrDefault(res, `Error consultando el estado (${res.status}).`));
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Arranca el análisis y consulta su estado hasta que termina. Reporta el progreso
// vía onProgress para la barra. Un fallo transitorio de red al consultar no aborta:
// se reintenta hasta el tope de seguridad (Render gratis puede tardar en despertar).
export async function runBocaDeUrnaAsync(
  req: UrnaRequest,
  onProgress?: (p: UrnaProgress) => void,
): Promise<UrnaResponse> {
  const jobId = await startBocaDeUrna(req);
  const started = Date.now();
  for (;;) {
    await sleep(POLL_INTERVAL_MS);
    let st: UrnaJobStatus;
    try {
      st = await fetchStatus(jobId);
    } catch (e) {
      if (Date.now() - started > MAX_WAIT_MS) throw e;
      continue;  // reintento transitorio
    }
    if (st.state === "done" && st.result) return st.result;
    if (st.state === "error") throw new Error(st.error || "El análisis falló.");
    onProgress?.(st.progress);
    if (Date.now() - started > MAX_WAIT_MS) throw new Error("El análisis tardó demasiado. Reintentá.");
  }
}
