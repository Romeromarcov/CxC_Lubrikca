import React, { useEffect, useState } from "react";
import type { LubrikcaApiClient } from "@lubrikca/domain-ts";

interface Props {
  api: LubrikcaApiClient;
  miCuentaId: number;
}

interface CuentaEmpresa {
  cuenta_id: number;
  email: string;
  rol_portal: string;
  activo: boolean;
  ultimo_acceso_en: string | null;
}

export function EquipoPortalView({ api, miCuentaId }: Props): React.JSX.Element {
  const [cuentas, setCuentas] = useState<CuentaEmpresa[]>([]);
  const [email, setEmail] = useState("");
  const [rol, setRol] = useState<"compras" | "tesoreria" | "titular">("compras");
  const [enlace, setEnlace] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function cargar(): Promise<void> {
    const res = await api.portalListarUsuarios();
    setCuentas((res.items as CuentaEmpresa[]) || []);
  }

  useEffect(() => {
    void cargar().catch((e) => setErr(e instanceof Error ? e.message : "Error cargando equipo."));
  }, [api]);

  async function invitar(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    setEnlace(null);
    try {
      const resp = await api.portalInvitarUsuario({
        email: email.trim().toLowerCase(),
        rol_portal: rol,
      });
      setEnlace(String(resp.enlace_activacion || ""));
      setEmail("");
      await cargar();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "No se pudo invitar al usuario.");
    }
  }

  async function alternarActivo(c: CuentaEmpresa): Promise<void> {
    await api.portalActualizarUsuario(c.cuenta_id, { activo: !c.activo });
    await cargar();
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Equipo de Acceso de tu Empresa</h2>
      <form className="card" onSubmit={(e) => void invitar(e)}>
        <h3 style={{ marginTop: 0 }}>Invitar usuario (enlace válido por 48h)</h3>
        {err ? <div className="badge badge-err" style={{ display: "block", marginBottom: 8 }}>{err}</div> : null}
        {enlace ? (
          <div className="badge badge-ok" style={{ display: "block", marginBottom: 8 }}>
            Enlace generado: {enlace}
          </div>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            className="input"
            style={{ flex: 2, marginBottom: 0 }}
            type="email"
            required
            placeholder="correo@tuempresa.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <select
            className="select"
            style={{ flex: 1, marginBottom: 0 }}
            value={rol}
            onChange={(e) => setRol(e.target.value as "compras" | "tesoreria" | "titular")}
          >
            <option value="compras">Compras (Catálogo y Pedidos)</option>
            <option value="tesoreria">Tesorería (Estado de Cuenta y Pagos)</option>
            <option value="titular">Titular (Acceso completo)</option>
          </select>
          <button type="submit" className="btn btn-primary">Generar Invitación</button>
        </div>
      </form>

      <div className="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Correo</th>
              <th>Rol</th>
              <th>Estado</th>
              <th>Último Acceso</th>
              <th>Acción</th>
            </tr>
          </thead>
          <tbody>
            {cuentas.map((c) => (
              <tr key={c.cuenta_id}>
                <td>{c.email}</td>
                <td>{c.rol_portal}</td>
                <td>
                  <span className={`badge ${c.activo ? "badge-ok" : "badge-err"}`}>
                    {c.activo ? "Activo" : "Revocado"}
                  </span>
                </td>
                <td>{c.ultimo_acceso_en || "—"}</td>
                <td>
                  {c.cuenta_id !== miCuentaId ? (
                    <button
                      type="button"
                      className={`btn btn-sm ${c.activo ? "btn-danger" : ""}`}
                      onClick={() => void alternarActivo(c)}
                    >
                      {c.activo ? "Revocar" : "Reactivar"}
                    </button>
                  ) : (
                    <span style={{ color: "var(--muted)", fontSize: "0.78rem" }}>Tu cuenta</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
