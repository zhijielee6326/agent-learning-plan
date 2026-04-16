import urllib.request, json
r = urllib.request.urlopen('http://localhost:6333/collections/insurance_clauses')
data = json.loads(r.read().decode())
print(f"Points in Qdrant: {data['result']['points_count']}")