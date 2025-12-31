import json
from main import app

def export():
    print("Exporting OpenAPI specification...")
    openapi_data = app.openapi()
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(openapi_data, f, indent=2, ensure_ascii=False)
    print("Successfully created openapi.json")

if __name__ == "__main__":
    export()
