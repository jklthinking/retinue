/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO_MODE?: string;
  readonly VITE_DEMO_TODAY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
