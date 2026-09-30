"""Merge additive social routes into the existing reviewed OpenAPI 3.0 contract."""
from pathlib import Path
import yaml
from app.main import create_app


def compatible(value):
    if isinstance(value, list):
        return [compatible(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: compatible(item) for key, item in value.items()}
    if 'const' in value:
        value['enum'] = [value.pop('const')]
    options = value.get('anyOf', [])
    if len(options) == 2 and any(item.get('type') == 'null' for item in options):
        item = next(item for item in options if item.get('type') != 'null')
        value.pop('anyOf')
        if '$ref' in item:
            value['allOf'] = [item]
        else:
            value.update(item)
        value['nullable'] = True
    return value


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2] / 'openapi.yaml'
    spec = yaml.safe_load(root.read_text())
    app = create_app()
    generated = app.openapi()
    for path, methods in generated['paths'].items():
        for method, operation in methods.items():
            if 'Social publishing' in operation.get('tags', []):
                converted = compatible(operation)
                converted['security'] = [] if path.endswith('/callback') else [{'bearerAuth': []}]
                spec['paths'].setdefault(path, {})[method] = converted
    for name, schema in generated['components']['schemas'].items():
        if name not in spec['components']['schemas']:
            spec['components']['schemas'][name] = compatible(schema)
    spec['info']['version'] = '1.4.0'
    root.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=110))
    app.state.social_http.close()
    app.state.database.dispose()
