# Testdata — smoke test real de `vision.py`

`feed_falso.html` es un feed estilo X con contenido **inventado y conocido**, para
verificar el prompt de visión contra Gemini REAL (el unit test usa mocks; esto
prueba la extracción de verdad). La captura `feed_falso.png` se genera
screenshoteando el HTML (Playwright o cualquier navegador, ancho ~950px,
captura full-page: el `.feed` tiene 780px con `overflow:hidden`, que corta el
último post a mitad de texto — ese corte es intencional, testea la regla de
ignorar posts cortados).

## Cómo correrlo

Desde la raíz del repo (PowerShell), con la API key del backend:

    Get-Content backend\.env | ForEach-Object {
      if ($_ -match '^([^=#]+)=(.*)$') { Set-Item "env:$($matches[1].Trim())" $matches[2].Trim() } }
    $env:VISION_MIN_INTERVAL = "0"
    backend\.venv\Scripts\python.exe scraper_local\vision.py scraper_local\testdata\feed_falso.png --red twitter

## Resultado esperado

| # | autor | es_electoral | candidatos (postura) |
|---|-------|--------------|----------------------|
| 1 | @LaJefaDelBarrio | true | Javier Milei (a_favor) |
| 2 | @PoliticaFederal | true | Axel Kicillof (en_contra) |
| 3 | @DataElectoralAR | true | Javier Milei (a_favor), Axel Kicillof (a_favor), Facundo Manes (en_contra) — regla de encuestas: punteros a_favor, marginal en_contra; "indecisos" NO es candidato |
| 4 | @AgendaCongreso | true | Sergio Massa (neutro) |
| 5 | @FanaticoDelFobal | false | — |

**NO deben aparecer:** el post "Promocionado" (@TiendaOfertasAR), el sidebar
"A quién seguir", ni el post cortado (@PostCortado).

Criterio de aprobación: los 5 posts de la tabla extraídos con su autor y postura;
cero posts prohibidos. La fecha puede variar en formato ("2 h" vs "2h") — no es
criterio. Si Gemini falla una postura, ajustar el PROMPT en `vision.py` (no la
regla compartida `REGLAS_CANDIDATOS`, que es del clasificador de texto también) y
re-correr.
