"""Probe: do company career SEARCH pages list job links we can match by title? (read only)"""
from playwright.sync_api import sync_playwright
T = {"IBM": "https://www.ibm.com/careers/search?q=Application%20Developer%20AWS%20Cloud%20FullStack",
     "Cognizant": "https://careers.cognizant.com/india-en/jobs/?keyword=Python%20Developer",
     "Capgemini": "https://www.capgemini.com/in-en/careers/join-capgemini/job-search/?search=Software%20Engineer",
     "Accenture": "https://www.accenture.com/in-en/careers/jobsearch?jk=software%20engineer",
     "Wipro": "https://careers.wipro.com/search/?q=python&locationsearch=India"}
JS = """()=>Array.from(document.querySelectorAll('a[href]')).filter(a=>a.offsetParent&&(a.innerText||'').trim().length>12&&/job|requisition|JobDetail|details|position/i.test(a.href)).map(a=>[(a.innerText||'').trim().replace(/\s+/g,' ').slice(0,60),a.href.slice(0,120)]).slice(0,5)"""
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for k, u in T.items():
        pg = b.new_page(viewport={"width": 1366, "height": 850})
        try:
            pg.goto(u, wait_until="domcontentloaded", timeout=40000)
            pg.wait_for_timeout(7000)
            links = pg.evaluate(JS)
            print(k, "links:", len(links), "title:", pg.title()[:50])
            for l in links:
                print("   ", l)
        except Exception as e:
            print(k, "ERR", str(e)[:100])
        pg.close()
    b.close()
