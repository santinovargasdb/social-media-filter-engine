const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Postura = "a_favor" | "en_contra" | "neutro";

export interface UrnaCandidato {
  nombre: string; pct: number; pos: number; neg: number; neu: number; menciones: number;
  // % de cada postura sobre las menciones de ESTE candidato (ej. 20/10/70).
  pos_pct: number; neg_pct: number; neu_pct: number;
}
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
}
export interface UrnaResponse {
  candidatos: UrnaCandidato[]; evidencia: UrnaEvidencia[];
  comparacion: UrnaComparacion[]; meta: UrnaMeta;
}
export interface UrnaRequest {
  keywords: string[]; networks: string[]; date: string | null; country: string;
  pollster_csv: string; auto_consultoras: boolean;
}

export type UrnaStatus = "connecting" | "waking";
const TIMEOUT_MS = 120_000;

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

export async function runBocaDeUrna(
  req: UrnaRequest,
  onStatus?: (s: UrnaStatus) => void,
): Promise<UrnaResponse> {
  const attempt = async (): Promise<UrnaResponse> => {
    const res = await post("/api/boca-de-urna", req, TIMEOUT_MS);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json())?.detail || ""; } catch { /* sin body */ }
      const err = new Error(detail || `Falló: ${res.status} ${res.statusText}`) as Error & { fromResponse?: boolean };
      err.fromResponse = true;
      throw err;
    }
    return res.json();
  };

  onStatus?.("connecting");
  try {
    return await attempt();
  } catch {
    onStatus?.("waking");
    try {
      return await attempt();
    } catch (e) {
      const err = e as Error & { fromResponse?: boolean };
      if (err?.fromResponse && err.message && !err.message.startsWith("Falló:")) throw new Error(err.message);
      throw new Error("No se pudo conectar con el servidor. Puede estar despertando del modo reposo; esperá unos segundos y reintentá.");
    }
  }
}
