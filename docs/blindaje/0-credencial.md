# 0 — La credencial en el historial

El plan lo llama «el ítem más urgente de toda esta lista y el único que no depende
de ninguna fase». Sigue sin hacerse, y no puedo hacerlo yo: rotar necesita acceso a
Railway, y purgar reescribe el historial de un repositorio compartido.

Lo que sí se puede hacer sin decisión es dejar de investigar cada vez. Este
documento es la medición exacta y el procedimiento listo para correr.

## Qué hay, medido

Se escaneó **el historial completo** —791 commits, todas las ramas— buscando URLs
con contraseña, claves de API de Odoo y tokens de Railway. Resultado:

| | dónde | alcance |
|---|---|---|
| **una credencial remota** | `scripts/monitor_railway_logs.ps1` | **269 commits** |
| dos de desarrollo (`localhost`) | `.env.example`, `.env.qa.example`, `docker-compose.yml`, `scripts/barreras.sh`, `.github/workflows/ci.yml` | 533 commits · **no son secretos** |

La remota apunta a `postgres@sakura.proxy.rlwy.net`, con una contraseña de 32
caracteres. **El valor no se escribe acá** — está en el repositorio y ese es el
problema; repetirlo en un documento que también se versiona lo empeoraría.

Los dos commits que la enmarcan:

| commit | fecha | qué hizo |
|---|---|---|
| `652baeb0` | **2026-08-01** | la introdujo — coincide con la fecha que el plan estimaba |
| `0ff3439f` | 2026-09-02 | la sacó del archivo, **no del historial** |

Todo commit entre esos dos devuelve el valor con un solo comando, porque el archivo
no cambió en el medio.

### Un detalle que hay que reconciliar

El commit que la introdujo dice **«Staging»**, no producción:

> `feat: chequeo combinado de salud de Staging (Postgres + compare_backends) cada 3h`

El plan habla de «la credencial de la base de producción». Puede ser que sean la
misma, que Staging y producción compartan instancia, o que **haya dos cosas que
rotar y solo una esté en el historial**. Esa es la primera pregunta a resolver, y
solo vos podés.

### Está publicada

`652baeb0` está en `origin`: en **`main`**, en **`develop`** y en la rama
`claude/cxc-lubrikca-unified-discounts-0gmsvn`. Localmente lo contienen además 5
worktrees de agente y el tag `respaldo-antes-de-quitar-ci`.

Eso importa para el paso 3: reescribir el historial deja divergentes **todos** esos
punteros, y hay que empujar con `--force-with-lease` a cada rama y recrear el tag.

## El procedimiento, en orden

El orden importa: **rotar primero**. Purgar antes de rotar deja una ventana en la
que la credencial vieja sigue siendo válida y ya no sabés cuál era para revocarla.

### 1 · Rotar (tuyo, en Railway)

Cambiar la contraseña de esa base en Railway y actualizar la variable de entorno de
los servicios que la usan. Al terminar, la credencial vieja no abre nada: el valor
del historial pasa de ser una llave a ser un dato inútil. **Con esto solo, el riesgo
principal ya está cerrado.**

### 2 · Verificar que nadie más la use

```bash
grep -rn "sakura.proxy.rlwy.net" --exclude-dir=.git .
```

Debería no dar nada en el árbol de trabajo. Si aparece, hay otro archivo que
actualizar antes de seguir.

### 3 · Purgar el historial (opcional, y es una reescritura)

Solo si querés que el valor viejo tampoco quede legible. Después del paso 1 esto es
higiene, no urgencia.

Con [`git-filter-repo`](https://github.com/newren/git-filter-repo) —más rápido y
mejor mantenido que `filter-branch`— sobre un **clon fresco**, nunca sobre este
directorio de trabajo:

```bash
git clone --mirror <url-del-repo> repo-purgado.git
cd repo-purgado.git
printf '%s==>CREDENCIAL_PURGADA\n' 'LA_CONTRASENA_VIEJA' > /tmp/reemplazos.txt
git filter-repo --replace-text /tmp/reemplazos.txt
rm /tmp/reemplazos.txt
```

`--replace-text` reemplaza el valor y deja el resto del archivo intacto, que es
mejor que borrar el archivo entero: el script tiene historia útil aparte del
descuido.

Después:

```bash
git push --force --all
git push --force --tags
```

Y avisar a todo el que tenga un clon: su historia local queda divergente y tiene que
volver a clonar. Un `git pull` sobre el historial reescrito genera un merge que
**reintroduce los commits viejos**, credencial incluida.

### 4 · Lo que no se puede purgar

Si el repositorio está en GitHub, los commits viejos pueden seguir accesibles por
su SHA a través de la API incluso después del force-push, hasta que GitHub corra su
recolección. Hay que pedirles el purgado por soporte. **Esa es otra razón por la que
el paso 1 es el que importa**: es el único que no depende de que un tercero limpie
algo.

## Lo que sí quedó automatizado

`scripts/verificar_secretos.py`, enganchado como **primera** etapa de
`scripts/barreras.sh`. No arregla el historial: impide que vuelva a pasar.

```bash
python scripts/verificar_secretos.py            # el árbol de trabajo
python scripts/verificar_secretos.py --staged    # antes de commitear
python scripts/verificar_secretos.py --rango origin/main..HEAD
```

Tres decisiones de diseño que lo hacen usable:

- **Mira el host, no la forma.** Una URL con contraseña hacia `localhost` es la base
  de desarrollo y está en seis archivos a propósito. Marcar eso sería ruido, y un
  chequeo ruidoso se apaga.
- **Nunca imprime el valor.** Dice el archivo, la línea, el destino y de cuántos
  caracteres es la contraseña. Un chequeo de secretos que los imprime en el log de
  CI es peor que no tenerlo.
- **Un host de plantilla descarta la línea antes de mirar la contraseña.** Nadie
  tiene una base en un host llamado `host`. Sin esa regla, el docstring de
  `migrate_sheets_to_postgres.py` daba un falso positivo — apareció al escribirlo.

Verificado en las dos direcciones: pasa sobre los 390 archivos de hoy, y **falla
sobre `652baeb0`**, que es el caso real.
