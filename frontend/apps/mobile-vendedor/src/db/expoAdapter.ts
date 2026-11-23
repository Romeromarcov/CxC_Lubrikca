import * as SQLite from "expo-sqlite";

import type { SqlBind, SqlExecutor } from "./types";

export class ExpoSqliteExecutor implements SqlExecutor {
  private readonly db: SQLite.SQLiteDatabase;

  constructor(dbName: string = "lubrikca_vendedor.db") {
    this.db = SQLite.openDatabaseSync(dbName);
  }

  async exec(sql: string): Promise<void> {
    await this.db.execAsync(sql);
  }

  async run(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<{ changes: number; lastInsertRowId: number }> {
    const res = await this.db.runAsync(sql, [...params]);
    return {
      changes: res.changes,
      lastInsertRowId: res.lastInsertRowId,
    };
  }

  async getAll<T = Record<string, unknown>>(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<T[]> {
    return this.db.getAllAsync<T>(sql, [...params]);
  }

  async getFirst<T = Record<string, unknown>>(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<T | null> {
    return this.db.getFirstAsync<T>(sql, [...params]);
  }

  async withTransaction<T>(fn: () => Promise<T>): Promise<T> {
    let resultado!: T;
    await this.db.withTransactionAsync(async () => {
      resultado = await fn();
    });
    return resultado;
  }
}
