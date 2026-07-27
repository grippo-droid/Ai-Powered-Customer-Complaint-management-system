import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server runs on 5173, which is the origin allowed by the backend's
// CORS_ORIGINS setting. Change one and you must change the other.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
