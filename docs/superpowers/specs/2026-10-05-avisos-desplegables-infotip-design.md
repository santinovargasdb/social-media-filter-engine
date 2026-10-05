# Avisos colapsables + ⓘ explicativo en consultoras

**Fecha:** 2026-10-05
**Página:** `frontend/app/boca-de-urna/page.tsx`

## Problema

1. Los `meta.warnings` de la corrida (lecturas fallidas de Gemini, capturas de
   comentarios sin tomar, hashtags inexistentes) se renderizan como una lista
   gris apenas debajo del disclaimer. Son jerga técnica: un usuario del Depto.
   de Prensa los lee y cree que **la app está rota**, cuando en realidad el
   análisis salió bien (p. ej. 2 lecturas fallidas de Gemini sobre 115 posts).
2. La tabla "Redes vs consultoras" muestra "Apoyo en redes", brechas y colores
   sin que nadie que no haya construido la feature entienda qué significan.

## Decisión de diseño (aprobada por el usuario)

### 1. Avisos — "todo abajo + contador arriba"

- **Arriba** (junto al chip de "🕒 Última actualización"): un chip discreto
  `ⓘ N avisos`. No aparece si `warnings.length === 0`. Es un botón.
- **Al fondo de la página** (después de "Evolución de la conversación"): un
  componente `WarningsDetails` con `<details>/<summary>` nativo, cerrado por
  defecto. Summary: "Avisos de la corrida (N)". Adentro, la lista de warnings
  tal cual viene del backend, gris y chica. Nota de pie: aclaración de que
  son detalles técnicos y no afectan la validez del termómetro.
- **Interacción:** click en el chip de arriba → abre el `<details>` (estado
  controlado) y hace `scrollIntoView` hasta él.
- El cartel rojo de **error de conexión** NO cambia: eso sí es "app rota" y
  debe verse de entrada.

### 2. ⓘ explicativo en "Redes vs consultoras"

- Componente nuevo reutilizable `InfoTip`: botón ⓘ circulado chico que al
  clickear abre un popover flotante; se cierra con click afuera o Escape.
  Sin librerías (posicionamiento absoluto + `useEffect` para el click-outside).
- Se coloca junto al `<h3>Redes vs consultoras</h3>` en `page.tsx`.
- Contenido del popover, en lenguaje llano:
  - **Apoyo en redes:** de cada 100 menciones positivas, cuántas se lleva cada
    candidato — comparable con la intención de voto de una encuesta.
  - **Columnas de consultoras:** lo que midió cada una; el número entre
    paréntesis es la brecha con redes, en puntos.
  - **Colores de la brecha:** verde ≤3 pts (coinciden), amarillo ≤8
    (moderada), rojo >8 (grande).
  - **Cierre:** es un termómetro de conversación en redes, no una encuesta.
- La letra chica bajo la tabla (`ComparisonTable.tsx`) se acorta: queda solo la
  base de menciones positivas y la explicación del ↗ para abrir fuentes. El
  resto se muda al popover (no duplicar).

## Componentes

| Archivo | Cambio |
|---|---|
| `components/urna/InfoTip.tsx` | NUEVO — ⓘ + popover genérico (`children` como contenido) |
| `components/urna/WarningsDetails.tsx` | NUEVO — `<details>` colapsable controlado al fondo |
| `app/boca-de-urna/page.tsx` | chip `ⓘ N avisos` arriba, `WarningsDetails` al fondo, estado `avisosOpen` + ref para scroll, `InfoTip` junto al h3 de consultoras |
| `components/urna/ComparisonTable.tsx` | acortar la nota de pie |

Sin cambios de backend: todo lee `data.meta.warnings` como hoy. El aviso
"sin corrida ese día" sigue viniendo primero en la lista (store.py) — se verá
primero dentro del desplegable, que es correcto.

## Verificación

- `npm run build` (typecheck + build de Next).
- Dev server local apuntando al backend de producción (modo `stored`, no gasta
  cuota de SerpAPI/Gemini); verificación visual con Playwright: chip visible con
  N correcto, click abre y scrollea al desplegable, popover ⓘ abre/cierra con
  click-afuera y Escape, en claro y oscuro.
