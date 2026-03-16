import copy
import json
from typing import Any, Dict, List, Set

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="MAGNET Swagger Prototype", layout="wide")


# =========================
# CONFIG
# =========================
SWAGGER_JSON_URL = "http://localhost:8080/openapi.json"  # trocar para o endpoint real
MAGNET_PERMISSIONS_URL = "http://localhost:8080/api/me/permissions"  # trocar para o endpoint real

# Mapeamento de permissões por rota/operação.
# Pode ser movido depois para config externa, BD, ou metadata no próprio OpenAPI.
ROUTE_PERMISSION_MAP = {
    "/users": {
        "get": ["users.read"],
        "post": ["users.write"],
    },
    "/users/{id}": {
        "get": ["users.read"],
        "put": ["users.write"],
        "delete": ["users.delete"],
    },
    "/orders": {
        "get": ["orders.read"],
        "post": ["orders.write"],
    },
    "/orders/{id}": {
        "get": ["orders.read"],
        "patch": ["orders.write"],
        "delete": ["orders.delete"],
    },
    "/admin/health": {
        "get": ["admin.read"],
    },
}


# =========================
# HELPERS
# =========================
def get_mock_permissions(user_profile: str) -> List[str]:
    """
    Simulação local de permissões vindas do MAGNET.
    """
    profiles = {
        "viewer": ["users.read", "orders.read"],
        "editor": ["users.read", "users.write", "orders.read", "orders.write"],
        "admin": [
            "users.read",
            "users.write",
            "users.delete",
            "orders.read",
            "orders.write",
            "orders.delete",
            "admin.read",
        ],
    }
    return profiles.get(user_profile, [])


def get_permissions_from_magnet(token: str | None = None) -> List[str]:
    """
    Exemplo de chamada real ao MAGNET.
    Espera algo do género:
    {
        "permissions": ["users.read", "orders.read"]
    }
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(MAGNET_PERMISSIONS_URL, headers=headers, timeout=10)
    response.raise_for_status()

    data = response.json()
    return data.get("permissions", [])


def fetch_openapi_spec() -> Dict[str, Any]:
    response = requests.get(SWAGGER_JSON_URL, timeout=10)
    response.raise_for_status()
    return response.json()


def is_operation_allowed(
    path: str,
    method: str,
    user_permissions: Set[str],
    route_permission_map: Dict[str, Dict[str, List[str]]],
) -> bool:
    """
    Regra:
    - se não houver mapeamento explícito para a operação, por defeito fica visível
    - se houver mapeamento, o utilizador precisa de ter TODAS as permissões definidas
    """
    method = method.lower()
    path_rules = route_permission_map.get(path, {})
    required_permissions = path_rules.get(method)

    if not required_permissions:
        return True

    return all(permission in user_permissions for permission in required_permissions)


def filter_openapi_by_permissions(
    openapi_spec: Dict[str, Any],
    user_permissions: List[str],
    route_permission_map: Dict[str, Dict[str, List[str]]],
) -> Dict[str, Any]:
    """
    Cria uma cópia do spec OpenAPI contendo apenas operações permitidas.
    Também limpa tags não usadas.
    """
    filtered_spec = copy.deepcopy(openapi_spec)
    user_permissions_set = set(user_permissions)

    filtered_paths: Dict[str, Any] = {}

    for path, operations in openapi_spec.get("paths", {}).items():
        allowed_operations = {}

        for method, operation_data in operations.items():
            # Ignorar chaves OpenAPI que não são métodos HTTP
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                allowed_operations[method] = operation_data
                continue

            if is_operation_allowed(path, method, user_permissions_set, route_permission_map):
                allowed_operations[method] = operation_data

        if any(
            key.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
            for key in allowed_operations.keys()
        ):
            filtered_paths[path] = allowed_operations
        else:
            # Mantém apenas campos não-HTTP, se existirem e fizer sentido
            non_http = {
                k: v
                for k, v in allowed_operations.items()
                if k.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}
            }
            if non_http:
                filtered_paths[path] = non_http

    filtered_spec["paths"] = filtered_paths

    # Limpeza de tags não utilizadas
    used_tags = set()
    for _, operations in filtered_paths.items():
        for method, operation_data in operations.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}:
                for tag in operation_data.get("tags", []):
                    used_tags.add(tag)

    if "tags" in filtered_spec:
        filtered_spec["tags"] = [tag for tag in filtered_spec["tags"] if tag.get("name") in used_tags]

    return filtered_spec


def render_swagger_ui(openapi_spec: Dict[str, Any], height: int = 900) -> None:
    """
    Embebe Swagger UI via HTML component.
    """
    spec_json = json.dumps(openapi_spec)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist/swagger-ui.css" />
      <style>
        body {{
          margin: 0;
          background: white;
        }}
        #swagger-ui {{
          max-width: 100%;
        }}
      </style>
    </head>
    <body>
      <div id="swagger-ui"></div>

      <script src="https://unpkg.com/swagger-ui-dist/swagger-ui-bundle.js"></script>
      <script>
        const spec = {spec_json};

        window.ui = SwaggerUIBundle({{
          spec: spec,
          dom_id: '#swagger-ui',
          deepLinking: true,
          presets: [
            SwaggerUIBundle.presets.apis,
          ],
          layout: "BaseLayout",
          docExpansion: "list",
          filter: true,
          tryItOutEnabled: false
        }});
      </script>
    </body>
    </html>
    """

    components.html(html, height=height, scrolling=True)


# =========================
# UI
# =========================
st.title("Protótipo Swagger por Permissões - MAGNET")

st.markdown(
    """
Este protótipo lê um **OpenAPI/Swagger**, aplica **filtro por permissões** e renderiza
um **Swagger UI** apenas com as rotas autorizadas.
"""
)

with st.sidebar:
    st.header("Configuração")

    mode = st.radio(
        "Fonte das permissões",
        ["Mock", "MAGNET real"],
        index=0,
    )

    token = None
    selected_profile = None

    if mode == "Mock":
        selected_profile = st.selectbox("Perfil simulado", ["viewer", "editor", "admin"], index=0)
        permissions = get_mock_permissions(selected_profile)
    else:
        token = st.text_input("Bearer token", type="password")
        try:
            permissions = get_permissions_from_magnet(token)
        except Exception as exc:
            st.error(f"Erro ao obter permissões do MAGNET: {exc}")
            permissions = []

    st.subheader("Permissões ativas")
    st.code(json.dumps(permissions, indent=2, ensure_ascii=False), language="json")

    st.subheader("Mapeamento rota → permissões")
    st.code(json.dumps(ROUTE_PERMISSION_MAP, indent=2, ensure_ascii=False), language="json")


col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("OpenAPI")
    try:
        openapi_spec = fetch_openapi_spec()
        st.success("OpenAPI carregado com sucesso")
    except Exception as exc:
        st.error(f"Erro ao carregar OpenAPI: {exc}")
        openapi_spec = None

    if openapi_spec:
        filtered_spec = filter_openapi_by_permissions(
            openapi_spec=openapi_spec,
            user_permissions=permissions,
            route_permission_map=ROUTE_PERMISSION_MAP,
        )

        total_paths = len(openapi_spec.get("paths", {}))
        filtered_paths = len(filtered_spec.get("paths", {}))

        st.metric("Paths originais", total_paths)
        st.metric("Paths visíveis", filtered_paths)

        with st.expander("Spec filtrado", expanded=False):
            st.json(filtered_spec)

with col2:
    st.subheader("Swagger UI filtrado")
    if openapi_spec:
        render_swagger_ui(filtered_spec, height=950)