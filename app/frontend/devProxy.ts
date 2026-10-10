// The backend serves routes at the root (e.g. /journeys); the UI calls /api/*.
export const apiProxy = {
  '/api': {
    target: 'http://localhost:8000',
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
};
