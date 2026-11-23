import React, { useState } from "react";
import type { AuthTokensResponse, LubrikcaApiClient } from "@lubrikca/domain-ts";

interface Props {
  api: LubrikcaApiClient;
  initialTokenInvitacion: string;
  onAuthenticated: (auth: AuthTokensResponse) => void;
}

export function AuthView({
  api,
  initialTokenInvitacion,
  onAuthenticated,
}: Props): React.JSX.Element {
  const [modo, setModo] = useState<"login" | "activar">(
    initialTokenInvitacion ? "activar" : "login",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tokenInv, setTokenInv] = useState(initialTokenInvitacion);
  const [telefono, setTelefono] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    setLoading(true);
    try {
      const resp = await api.loginPortal({
        email: email.trim().toLowerCase(),
        password,
        device_id: "portal-web-pwa",
        plataforma: "web_pwa",
      });
      onAuthenticated(resp);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "No se pudo iniciar sesión.");
    } finally {
      setLoading(false);
    }
  }

  async function handleActivar(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    setLoading(true);
    try {
      await api.activarInvitacionPortal({
        token: tokenInv.trim(),
        password,
        telefono: telefono.trim() || undefined,
      });
      setModo("login");
      setMsg("Cuenta activada con éxito. Inicia sesión con tu correo y nueva contraseña.");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "No se pudo activar la invitación.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 440, margin: "42px auto", padding: "0 14px" }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Portal de Clientes · Lubrikca</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          Estado de cuenta BCV/Binance, pedidos en línea y reporte de pagos.
        </p>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button
            type="button"
            className={`btn btn-tab ${modo === "login" ? "active" : ""}`}
            style={{ flex: 1 }}
            onClick={() => setModo("login")}
          >
            Iniciar Sesión
          </button>
          <button
            type="button"
            className={`btn btn-tab ${modo === "activar" ? "active" : ""}`}
            style={{ flex: 1 }}
            onClick={() => setModo("activar")}
          >
            Activar Invitación
          </button>
        </div>

        {msg ? <div className="badge badge-ok" style={{ display: "block", marginBottom: 8 }}>{msg}</div> : null}
        {err ? <div className="badge badge-err" style={{ display: "block", marginBottom: 8 }}>{err}</div> : null}

        {modo === "login" ? (
          <form onSubmit={(e) => void handleLogin(e)}>
            <input
              className="input"
              type="email"
              required
              placeholder="Correo autorizado del portal"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <input
              className="input"
              type="password"
              required
              placeholder="Contraseña"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="submit" className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
              {loading ? "Validando..." : "Entrar al Portal"}
            </button>
          </form>
        ) : (
          <form onSubmit={(e) => void handleActivar(e)}>
            <input
              className="input"
              required
              placeholder="Token de invitación (vigencia 48h)"
              value={tokenInv}
              onChange={(e) => setTokenInv(e.target.value)}
            />
            <input
              className="input"
              placeholder="Teléfono de contacto (opcional)"
              value={telefono}
              onChange={(e) => setTelefono(e.target.value)}
            />
            <input
              className="input"
              type="password"
              required
              minLength={6}
              placeholder="Define tu contraseña (mín. 6 caracteres)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="submit" className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
              {loading ? "Activando..." : "Activar Acceso"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
