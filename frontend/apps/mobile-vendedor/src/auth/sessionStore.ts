import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";
import type { AuthTokensResponse, LubrikcaApiClient } from "@lubrikca/domain-ts";

import type { MobileSqliteRepo } from "../db/sqliteRepo";

export interface SecureKv {
  getItemAsync(key: string): Promise<string | null>;
  setItemAsync(key: string, value: string): Promise<void>;
  deleteItemAsync(key: string): Promise<void>;
}

export interface SessionTokens {
  access_token: string;
  refresh_token: string;
  device_id: string;
  user_email: string;
  user_nombre: string;
  user_rol: string;
}

const KEY_SESSION = "lubrikca_v1_session";
const KEY_BIOMETRIA = "lubrikca_v1_biometria_activa";

export class MobileSessionStore {
  private readonly kv: SecureKv;

  constructor(kv: SecureKv = SecureStore) {
    this.kv = kv;
  }

  async guardarSesion(resp: AuthTokensResponse): Promise<SessionTokens> {
    const data: SessionTokens = {
      access_token: resp.access_token,
      refresh_token: resp.refresh_token,
      device_id: resp.device_id,
      user_email: resp.user?.email ?? "",
      user_nombre: resp.user?.nombre ?? "",
      user_rol: resp.user?.rol ?? "ventas",
    };
    await this.kv.setItemAsync(KEY_SESSION, JSON.stringify(data));
    return data;
  }

  async leerSesion(): Promise<SessionTokens | null> {
    const raw = await this.kv.getItemAsync(KEY_SESSION);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as SessionTokens;
    } catch {
      return null;
    }
  }

  async limpiarTokens(): Promise<void> {
    await this.kv.deleteItemAsync(KEY_SESSION);
  }

  async configurarBiometria(activa: boolean): Promise<void> {
    await this.kv.setItemAsync(KEY_BIOMETRIA, activa ? "1" : "0");
  }

  async biometriaActiva(): Promise<boolean> {
    return (await this.kv.getItemAsync(KEY_BIOMETRIA)) === "1";
  }

  async verificarBiometriaSiDisponible(): Promise<boolean> {
    if (!(await this.biometriaActiva())) return true;
    const hasHw = await LocalAuthentication.hasHardwareAsync();
    const enrolled = await LocalAuthentication.isEnrolledAsync();
    if (!hasHw || !enrolled) return true;
    const res = await LocalAuthentication.authenticateAsync({
      promptMessage: "Confirma tu identidad para abrir Lubrikca Vendedores",
      cancelLabel: "Cancelar",
    });
    return res.success;
  }
}

export async function cerrarSesionYLimpiarDispositivo(
  api: LubrikcaApiClient | null,
  sessionStore: MobileSessionStore,
  repo: MobileSqliteRepo,
): Promise<void> {
  const actual = await sessionStore.leerSesion();
  if (api && actual) {
    try {
      await api.logoutVendedor(actual.refresh_token, actual.device_id);
    } catch {
      // Si el dispositivo está offline, de todos modos limpiamos tokens y SQLite local.
    }
  }
  await sessionStore.limpiarTokens();
  await repo.wipeAllTables();
}
