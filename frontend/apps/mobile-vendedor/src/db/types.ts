export type SqlBind = string | number | null;

export interface SqlExecutor {
  exec(sql: string): Promise<void>;
  run(sql: string, params?: ReadonlyArray<SqlBind>): Promise<{ changes: number; lastInsertRowId: number }>;
  getAll<T = Record<string, unknown>>(sql: string, params?: ReadonlyArray<SqlBind>): Promise<T[]>;
  getFirst<T = Record<string, unknown>>(sql: string, params?: ReadonlyArray<SqlBind>): Promise<T | null>;
  withTransaction<T>(fn: () => Promise<T>): Promise<T>;
}
