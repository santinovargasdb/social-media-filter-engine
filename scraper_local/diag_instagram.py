"""Diagnóstico: abre Instagram con la sesión ig1 y vuelca los elementos
del sidebar para encontrar el selector correcto del ícono de búsqueda."""
import json
from pathlib import Path
import accounts
import browser

sesion = accounts.ruta_sesion("ig1", "instagram")
print(f"Sesión: {sesion}")

with browser.pagina_con_sesion(sesion, (1280, 900), headless=False) as page:
    page.goto("https://www.instagram.com/", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    browser.esperar_aleatorio((3, 5))

    # Screenshot para ver qué está pasando
    screenshot = Path("diag_ig_home.png")
    page.screenshot(path=str(screenshot), full_page=False)
    print(f"\nScreenshot guardado: {screenshot}")

    # Volcar todos los links/botones del sidebar con sus atributos
    elementos = page.evaluate("""() => {
        const els = document.querySelectorAll('a, button, [role="button"], [role="link"], nav *[aria-label]');
        return Array.from(els).slice(0, 60).map(el => ({
            tag: el.tagName,
            href: el.getAttribute('href'),
            ariaLabel: el.getAttribute('aria-label'),
            role: el.getAttribute('role'),
            text: el.innerText?.slice(0, 40),
            testid: el.getAttribute('data-testid'),
        }));
    }""")

    print("\n--- Elementos interactivos (primeros 60) ---")
    for e in elementos:
        aria = e.get('ariaLabel')
        href = e.get('href')
        text = e.get('text', '').strip().replace('\n', ' ')
        testid = e.get('testid')
        if aria or href or testid:
            print(f"  {e['tag']:8} aria={aria!r:30} href={str(href)[:40]:40} text={text[:30]!r} testid={testid!r}")

    # Buscar específicamente algo relacionado a "buscar" o "search"
    print("\n--- Candidatos para icono búsqueda ---")
    candidatos = page.evaluate("""() => {
        const terms = ['buscar', 'search', 'explore', 'lupa'];
        const all = document.querySelectorAll('*');
        const found = [];
        for (const el of all) {
            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
            const href = (el.getAttribute('href') || '').toLowerCase();
            const testid = (el.getAttribute('data-testid') || '').toLowerCase();
            if (terms.some(t => aria.includes(t) || href.includes(t) || testid.includes(t))) {
                found.push({
                    tag: el.tagName,
                    aria: el.getAttribute('aria-label'),
                    href: el.getAttribute('href'),
                    testid: el.getAttribute('data-testid'),
                    outerHTML: el.outerHTML.slice(0, 200),
                });
            }
        }
        return found.slice(0, 20);
    }""")
    for c in candidatos:
        print(f"  {c['tag']} aria={c['aria']!r} href={c['href']!r} testid={c['testid']!r}")
        print(f"    HTML: {c['outerHTML'][:120]}")

    input("\nPresioná Enter para cerrar el browser...")
