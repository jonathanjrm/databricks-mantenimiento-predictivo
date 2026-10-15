"""Renders portfolio/carousel/carousel.html to png/slide-NN.png (1080x1350 at device scale factor 2)
and carousel.pdf (one page per slide). Uses the preinstalled Playwright Chromium; run from the repo root:
    python3 portfolio/carousel/render.py
"""
import os, sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "file://" + os.path.join(HERE, "carousel.html")
OUT = os.path.join(HERE, "png")
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1180, "height": 1450}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(SRC, wait_until="load")
    pg.evaluate("document.fonts.ready.then(()=>true)")
    pg.wait_for_timeout(800)
    # overflow check: any element whose box leaves its slide
    report = pg.evaluate("""() => {
      const out=[];
      document.querySelectorAll('.slide').forEach((s,i)=>{
        const r=s.getBoundingClientRect();
        s.querySelectorAll('*').forEach(e=>{
          if(e.closest('svg') && e.tagName!=='svg') return;
          const b=e.getBoundingClientRect(); if(!b.width||!b.height) return;
          if(b.right>r.right+0.5||b.bottom>r.bottom+0.5||b.left<r.left-0.5) out.push([i+1,e.tagName,e.className&&e.className.baseVal!==undefined?e.className.baseVal:e.className,Math.round(b.right-r.right),Math.round(b.bottom-r.bottom),(e.textContent||'').trim().slice(0,40)]);
        });
        // text overflow inside elements with fixed width
        s.querySelectorAll('p,li,h1,h2,pre,span,td,th').forEach(e=>{ if(e.scrollWidth>e.clientWidth+1 && getComputedStyle(e).overflow!=='visible') out.push([i+1,'SCROLL',e.tagName,e.scrollWidth-e.clientWidth,0,(e.textContent||'').trim().slice(0,40)]); });
      });
      return out; }""")
    for r in report:
        print("OVERFLOW", r)
    fonts = pg.evaluate("[...document.fonts].filter(f=>f.status==='loaded').map(f=>f.family+' '+f.weight)")
    print("fonts loaded:", sorted(set(fonts)))
    slides = pg.query_selector_all(".slide")
    for i, s in enumerate(slides, 1):
        s.screenshot(path=os.path.join(OUT, f"slide-{i:02d}.png"), type="png")
    print(f"{len(slides)} slides -> {OUT}")
    # PDF: one page per slide
    pg2 = ctx.new_page()
    pg2.goto(SRC, wait_until="load")
    pg2.wait_for_timeout(800)
    pg2.add_style_tag(content="@page{size:1080px 1350px;margin:0} html,body{background:#0B0E13} .deck{gap:0;padding:0} .slide{page-break-after:always;break-after:page}")
    pg2.emulate_media(media="screen")
    pg2.pdf(path=os.path.join(HERE, "carousel.pdf"), width="1080px", height="1350px", print_background=True, prefer_css_page_size=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
    print("pdf ->", os.path.join(HERE, "carousel.pdf"))
    b.close()
