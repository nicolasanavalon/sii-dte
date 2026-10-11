"""Lista (solo lectura) los PDF de la carpeta SUBLIMAR del OneDrive."""
import json, os, urllib.request, urllib.parse
from urllib.parse import quote

MS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
CARPETA = os.environ.get("ONEDRIVE_CARPETA", "SUBLIMAR")

def token():
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": MS_CLIENT_ID,
        "refresh_token": os.environ["MS_REFRESH_TOKEN"].strip(),
        "scope": "Files.Read offline_access User.Read",
    }).encode()
    req = urllib.request.Request("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", data=data)
    return json.load(urllib.request.urlopen(req, timeout=40))["access_token"]

AT = token()
def g(url):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + AT})
    return json.load(urllib.request.urlopen(req, timeout=60))

G = "https://graph.microsoft.com/v1.0/me/drive"
try:
    root = g(G + "/root:/" + quote(CARPETA))
except Exception as e:
    print("No está en la raíz (%s); buscando..." % e)
    hits = [h for h in g(G + "/root/search(q='%s')" % CARPETA)["value"] if h.get("folder")]
    for h in hits:
        print("  candidato:", h.get("parentReference", {}).get("path"), "/", h["name"])
    root = hits[0]

pdfs, otros = [], {}
stack = [(root["id"], "")]
while stack:
    fid, ruta = stack.pop()
    url = G + "/items/%s/children?$select=id,name,file,folder,size&$top=200" % fid
    while url:
        d = g(url)
        for it in d.get("value", []):
            p = ruta + "/" + it["name"]
            if it.get("folder"):
                stack.append((it["id"], p))
            elif it["name"].lower().endswith(".pdf"):
                pdfs.append((p, it.get("size", 0)))
            else:
                ext = it["name"].rsplit(".", 1)[-1].lower() if "." in it["name"] else "(sin ext)"
                otros[ext] = otros.get(ext, 0) + 1
        url = d.get("@odata.nextLink")

pdfs.sort()
print("===LISTADO===")
for p, s in pdfs:
    print("%8.1f KB  %s" % (s / 1024, p))
print("===RESUMEN===")
print("PDF: %d  (%.1f MB)" % (len(pdfs), sum(s for _, s in pdfs) / 1048576))
print("Otros archivos por tipo:", json.dumps(otros, ensure_ascii=False))
