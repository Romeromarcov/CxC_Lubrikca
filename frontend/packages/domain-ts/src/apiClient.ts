import type {
  AuthTokensResponse,
  BootstrapResponse,
  EstadoCuentaPortalResponse,
  LoteEfectivo,
  PushBatchResponse,
  PushItem,
  StockVerificarResponse,
  VendedorCarteraResponse,
} from "./types";

export class ApiHttpError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: Record<string, unknown> | null;

  constructor(status: number, detail: string, body: Record<string, unknown> | null = null) {
    super(detail);
    this.name = "ApiHttpError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

export interface ApiClientConfig {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  getAccessToken?: () => Promise<string | null> | string | null;
  getRefreshToken?: () => Promise<string | null> | string | null;
  onTokensRefreshed?: (tokens: AuthTokensResponse) => Promise<void> | void;
  onSessionRevoked?: (reason: string) => Promise<void> | void;
}

export class LubrikcaApiClient {
  private readonly baseUrl: string;
  private readonly fetchFn: typeof fetch;
  private readonly cfg: ApiClientConfig;
  private refreshEnCurso: Promise<string | null> | null = null;

  constructor(config: ApiClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.fetchFn = config.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.cfg = config;
  }

  private async requestJson<T>(
    path: string,
    init: RequestInit & { authMode?: "vendedor" | "portal" | "none" } = {},
  ): Promise<T> {
    const authMode = init.authMode ?? "vendedor";
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body && !(init.body instanceof Uint8Array)) {
      headers.set("Content-Type", "application/json");
    }

    if (authMode !== "none" && this.cfg.getAccessToken) {
      const tok = await this.cfg.getAccessToken();
      if (tok) headers.set("Authorization", `Bearer ${tok}`);
    }

    let res = await this.fetchFn(`${this.baseUrl}${path}`, { ...init, headers });
    if (res.status === 401 && authMode !== "none" && this.cfg.getRefreshToken) {
      const nuevoTok = await this.renovarToken(authMode);
      if (nuevoTok) {
        const retryHeaders = new Headers(headers);
        retryHeaders.set("Authorization", `Bearer ${nuevoTok}`);
        res = await this.fetchFn(`${this.baseUrl}${path}`, { ...init, headers: retryHeaders });
      }
    }

    const text = await res.text();
    let parsed: Record<string, unknown> | null = null;
    if (text) {
      try {
        parsed = JSON.parse(text) as Record<string, unknown>;
      } catch {
        parsed = null;
      }
    }

    if (!res.ok) {
      const detail =
        String(parsed?.detail ?? parsed?.error ?? res.statusText) || `HTTP ${res.status}`;
      if ((res.status === 401 || res.status === 403) && authMode !== "none" && this.cfg.onSessionRevoked) {
        if (
          res.status === 401 ||
          detail.toLowerCase().includes("revocad") ||
          detail.toLowerCase().includes("audiencia")
        ) {
          await this.cfg.onSessionRevoked(detail);
        }
      }
      throw new ApiHttpError(res.status, detail, parsed);
    }

    return (parsed ?? {}) as T;
  }

  private async renovarToken(mode: "vendedor" | "portal"): Promise<string | null> {
    if (this.refreshEnCurso) return this.refreshEnCurso;
    this.refreshEnCurso = (async () => {
      const rt = this.cfg.getRefreshToken ? await this.cfg.getRefreshToken() : null;
      if (!rt) return null;
      const ruta = mode === "portal" ? "/api/v1/portal/auth/refresh" : "/api/v1/auth/refresh";
      const res = await this.fetchFn(`${this.baseUrl}${ruta}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) {
        if (this.cfg.onSessionRevoked) {
          await this.cfg.onSessionRevoked("Refresh token expirado o revocado.");
        }
        return null;
      }
      const data = (await res.json()) as AuthTokensResponse;
      if (this.cfg.onTokensRefreshed) {
        await this.cfg.onTokensRefreshed(data);
      }
      return data.access_token;
    })().finally(() => {
      this.refreshEnCurso = null;
    });
    return this.refreshEnCurso;
  }

  loginVendedor(body: {
    email: string;
    password: string;
    device_id: string;
    plataforma: "android" | "ios" | "web_pwa";
    app_version?: string;
    push_token?: string;
  }): Promise<AuthTokensResponse> {
    return this.requestJson<AuthTokensResponse>("/api/v1/auth/login", {
      method: "POST",
      authMode: "none",
      body: JSON.stringify(body),
    });
  }

  logoutVendedor(refreshToken?: string | null, deviceId?: string | null): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/auth/logout", {
      method: "POST",
      authMode: "none",
      body: JSON.stringify({
        refresh_token: refreshToken ?? undefined,
        device_id: deviceId ?? undefined,
      }),
    });
  }

  syncBootstrap(): Promise<BootstrapResponse> {
    return this.requestJson<BootstrapResponse>("/api/v1/sync/bootstrap");
  }

  syncDelta(cursor: string): Promise<BootstrapResponse> {
    const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return this.requestJson<BootstrapResponse>(`/api/v1/sync/delta${qs}`);
  }

  syncPush(
    deviceId: string,
    items: PushItem[],
    idempotencyKeyHeader?: string,
  ): Promise<PushBatchResponse> {
    const headers: Record<string, string> = {};
    if (idempotencyKeyHeader) {
      headers["X-Idempotency-Key"] = idempotencyKeyHeader;
    }
    return this.requestJson<PushBatchResponse>("/api/v1/sync/push", {
      method: "POST",
      headers,
      body: JSON.stringify({ device_id: deviceId, items }),
    });
  }

  presignAdjunto(body: {
    sha256: string;
    mime: string;
    bytes: number;
    entidad_tipo?: string;
    entidad_id?: string;
  }): Promise<{
    adjunto_id?: number;
    ya_existia: boolean;
    upload_url?: string | null;
    upload_headers?: Record<string, string>;
    storage_key: string;
  }> {
    return this.requestJson("/api/v1/adjuntos/presign", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  verificarStock(params: { q?: string; producto_id?: string; refresh?: boolean } = {}): Promise<StockVerificarResponse> {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.producto_id) sp.set("producto_id", params.producto_id);
    if (params.refresh) sp.set("refresh", "true");
    const qs = sp.toString();
    return this.requestJson<StockVerificarResponse>(`/api/v1/stock/verificar${qs ? `?${qs}` : ""}`);
  }

  listarLotesEfectivo(limit = 100): Promise<{ total: number; items: LoteEfectivo[] }> {
    return this.requestJson(`/api/v1/vendedor/lotes-efectivo?limit=${limit}`);
  }

  obtenerCarteraVendedor(): Promise<VendedorCarteraResponse> {
    return this.requestJson<VendedorCarteraResponse>("/api/v1/vendedor/cartera");
  }

  // Portal de clientes
  loginPortal(body: {
    email: string;
    password: string;
    device_id?: string;
    plataforma?: "web_pwa" | "android" | "ios";
  }): Promise<AuthTokensResponse> {
    return this.requestJson<AuthTokensResponse>("/api/v1/portal/auth/login", {
      method: "POST",
      authMode: "none",
      body: JSON.stringify(body),
    });
  }

  activarInvitacionPortal(body: {
    token: string;
    password: string;
    telefono?: string;
  }): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/portal/auth/activar-invitacion", {
      method: "POST",
      authMode: "none",
      body: JSON.stringify(body),
    });
  }

  logoutPortal(refreshToken?: string | null, deviceId?: string | null): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/portal/auth/logout", {
      method: "POST",
      authMode: "none",
      body: JSON.stringify({
        refresh_token: refreshToken ?? undefined,
        device_id: deviceId ?? undefined,
      }),
    });
  }

  portalEstadoCuenta(): Promise<EstadoCuentaPortalResponse> {
    return this.requestJson<EstadoCuentaPortalResponse>("/api/v1/portal/estado-cuenta", {
      authMode: "portal",
    });
  }

  portalCatalogo(): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/portal/catalogo", { authMode: "portal" });
  }

  portalCuentasPago(ruta?: string): Promise<Record<string, unknown>> {
    const qs = ruta ? `?ruta=${encodeURIComponent(ruta)}` : "";
    return this.requestJson(`/api/v1/portal/cuentas-pago${qs}`, { authMode: "portal" });
  }

  portalCrearCotizacion(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/portal/cotizaciones", {
      method: "POST",
      authMode: "portal",
      body: JSON.stringify(payload),
    });
  }

  portalRegistrarPago(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.requestJson("/api/v1/portal/pagos", {
      method: "POST",
      authMode: "portal",
      body: JSON.stringify(payload),
    });
  }
}
