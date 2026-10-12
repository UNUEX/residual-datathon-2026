"""Local Residual workspace. Run: python -m src.serve --port 8766."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .service import Workspace
from .research import ResearchService, ResearchError
from .model_provider import ProviderError
from .tabular import TabularService

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 18 * 1024 * 1024

class Handler(BaseHTTPRequestHandler):
    @property
    def workspace(self):
        return self.server.workspace

    @property
    def research(self):
        return self.server.research

    @property
    def tables(self):
        return self.server.tables

    def reply(self, code, data, content_type='application/json; charset=utf-8', filename=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        for name, value in [('Content-Type', content_type), ('Cache-Control', 'no-store'),
                            ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                            ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]:
            self.send_header(name, value)
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Доступ только через локальный адрес.'})
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        run_id = query.get('run_id', [None])[0]
        try:
            if path == '/api/health':
                return self.reply(200, {'ok': True, 'app': 'residual', 'version': '0.4',
                                        'mode': 'local', 'assistant_mode': 'validated_research', 'table_import': True})
            if path == '/api/tables/state':
                return self.reply(200, self.tables.state())
            if path == '/api/tables/report':
                return self.reply(200, self.tables.report(query.get('id', [''])[0]))
            if path == '/api/tables/export':
                raw, mime, filename = self.tables.export(query.get('id', [''])[0], query.get('format', ['json'])[0])
                return self.reply(200, raw, mime, filename)
            if path == '/api/research/state':
                return self.reply(200, self.research.state())
            if path == '/api/research/report':
                return self.reply(200, self.research.report(query.get('id', [''])[0]))
            if path == '/api/research/export':
                raw, mime, filename = self.research.export(query.get('id', [''])[0], query.get('format', ['json'])[0])
                return self.reply(200, raw, mime, filename)
            if path == '/api/state':
                return self.reply(200, self.workspace.state(run_id))
            if path == '/api/runs':
                return self.reply(200, self.workspace.index())
            if path.startswith('/api/jobs/'):
                return self.reply(200, self.workspace.job(path.rsplit('/', 1)[-1]))
            if path == '/api/sources':
                return self.reply(200, self.workspace.state(run_id).get('sources', []))
            if path == '/api/export':
                raw, mime, filename = self.workspace.export(run_id, query.get('format', ['json'])[0])
                return self.reply(200, raw, mime + '; charset=utf-8', filename)
            if path == '/api/demo-file':
                form = query.get('form', [''])[0]
                return self.reply(200, self.workspace.demo_csv(form).encode('utf-8-sig'), 'text/csv; charset=utf-8', form + '-2024.csv')
            brief_assets = {'/brief-example/': ('index.html', 'text/html'),
                            '/brief-example/total-area.svg': ('total-area.svg', 'image/svg+xml'),
                            '/brief-example/area-per-person.svg': ('area-per-person.svg', 'image/svg+xml'),
                            '/brief-example/table.csv': ('table.csv', 'text/csv'),
                            '/brief-example/sensitivity.csv': ('sensitivity.csv', 'text/csv'),
                            '/brief-example/result.json': ('result.json', 'application/json'),
                            '/brief-example/reproduce.py': ('reproduce.py', 'text/plain')}
            if path in brief_assets:
                name, mime = brief_assets[path]
                content = (ROOT / 'examples' / 'research_brief' / name).read_bytes()
                download = name if name.endswith(('.csv', '.json', '.py')) else None
                return self.reply(200, content, mime + '; charset=utf-8', download)
            import_examples = {'/examples/import/schools.xlsx': ('schools.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                               '/examples/import/budget.html': ('budget.html', 'text/html'),
                               '/examples/import/schools.csv': ('schools.csv', 'text/csv')}
            if path in import_examples:
                name, mime = import_examples[path]
                return self.reply(200, (ROOT / 'examples' / 'import' / name).read_bytes(), mime, name)
            assets = {'/': ('tables.html', 'text/html'), '/housing/': ('index.html', 'text/html'),
                      '/tables.js': ('tables.js', 'text/javascript'), '/tables.css': ('tables.css', 'text/css'),
                      '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'),
                      '/research.js': ('research.js', 'text/javascript'), '/research.css': ('research.css', 'text/css'),
                      '/quality': ('quality.html', 'text/html'), '/quality/': ('quality.html', 'text/html'),
                      '/assets/residual-mark.png': ('assets/residual-mark.png', 'image/png')}
            if path in assets:
                name, mime = assets[path]
                content_type = mime if mime.startswith('image/') else mime + '; charset=utf-8'
                return self.reply(200, (ROOT / 'app' / name).read_bytes(), content_type)
            return self.reply(404, {'error': 'Страница не найдена.'})
        except ProviderError as exc:
            return self.reply(502, {'error': str(exc)})
        except (ValueError, ResearchError) as exc:
            return self.reply(400, {'error': str(exc)})
        except Exception as exc:
            self.log_error('Unhandled GET error: %s', type(exc).__name__)
            return self.reply(500, {'error': 'Не удалось прочитать локальные данные. Проверьте журнал сервера.'})

    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Доступ только через локальный адрес.'})
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if origin and origin not in allowed:
            return self.reply(403, {'error': 'Запрос пришёл с другого сайта.'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.reply(415, {'error': 'Ожидается JSON-запрос.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY:
                return self.reply(413, {'error': 'Превышен размер запроса. Для импорта одной таблицы выберите файл до 10 МБ.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Ожидается объект запроса.')
            routes = {'/api/tables/inspect': self.tables.inspect, '/api/tables/preview': self.tables.preview,
                      '/api/tables/register': self.tables.register, '/api/tables/plan': self.tables.plan,
                      '/api/tables/execute': self.tables.execute,
                      '/api/demo': self.workspace.start_demo, '/api/analyze': self.workspace.start_analysis,
                      '/api/review': self.workspace.review, '/api/assistant': self.workspace.assistant,
                      '/api/demo-recheck': self.workspace.demo_recheck,
                      '/api/research/demo': lambda _: self.research.load_demo(),
                      '/api/research/upload': self.research.upload,
                      '/api/research/configure': self.research.configure,
                      '/api/research/probe': lambda _: self.research.probe(),
                      '/api/research/plan': self.research.plan,
                      '/api/research/execute': self.research.execute}
            route = routes.get(urlparse(self.path).path)
            if route is None:
                return self.reply(404, {'error': 'Действие не найдено.'})
            return self.reply(200, route(body))
        except ProviderError as exc:
            return self.reply(502, {'error': str(exc)})
        except (ValueError, TypeError, ResearchError, json.JSONDecodeError) as exc:
            return self.reply(400, {'error': str(exc)})
        except Exception as exc:
            self.log_error('Unhandled POST error: %s', type(exc).__name__)
            return self.reply(500, {'error': 'Не удалось выполнить действие. Проверьте журнал сервера.'})

def make_server(port=8766, root=ROOT, data_dir=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.workspace = Workspace(root, data_dir)
    server.research = ResearchService(ROOT, Path(root) / 'private_outputs' / 'research')
    server.tables = TabularService(Path(root) / 'private_outputs' / 'tabular', lambda: server.research.provider)
    server.daemon_threads = True
    return server

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    server = make_server(args.port, data_dir=args.data_dir)
    print(f'Residual: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
