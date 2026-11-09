/// <reference types="vite/client" />

// @types/react is not installed in this project; declare the minimum surface the
// codebase uses so editors stop reporting false "JSX.IntrinsicElements" errors.
// Vite/esbuild strip types at build time, so this only affects tooling.
declare namespace JSX {
  interface IntrinsicElements {
    [element: string]: any;
  }
  interface Element {}
  interface ElementClass {}
  interface ElementAttributesProperty {}
  interface ElementChildrenAttribute {}
  interface IntrinsicAttributes {}
  interface IntrinsicClassAttributes<T> {}
}

declare module "react" {
  export type ReactNode = any;
  export type ReactElement = any;
  export type FC<P = {}> = (props: P) => any;
  export type Dispatch<A> = (value: A) => void;
  export type SetStateAction<S> = S | ((previous: S) => S);
  export type Key = string | number;
  export type ChangeEvent<T = any> = { target: T };
  export type FormEvent<T = any> = { preventDefault(): void; target: T };
  export type MouseEvent<T = any> = { clientX: number; clientY: number; button: number; target: T; preventDefault(): void; stopPropagation(): void };
  export type DragEvent<T = any> = { preventDefault(): void; dataTransfer: { files: FileList; setData(type: string, value: string): void; getData(type: string): string } };
  export type PointerEvent<T = any> = { clientX: number; clientY: number; pointerId: number; preventDefault(): void; stopPropagation(): void };
  export type Ref<T = any> = { current: T };
  export type MutableRefObject<T = any> = { current: T };
  export type CSSProperties = Record<string, any>;
  export type ElementType = any;
  export const createContext: any;
  export const useContext: any;
  export const useState: any;
  export const useEffect: any;
  export const useMemo: any;
  export const useCallback: any;
  export const useReducer: any;
  export const useRef: any;
  export const useImperativeHandle: any;
  export const forwardRef: any;
  const React: any;
  export default React;
}

declare module "react-dom" {
  const value: any;
  export default value;
}

declare module "react-dom/client" {
  export const createRoot: any;
}

declare module "react-router-dom" {
  export const BrowserRouter: any;
  export const MemoryRouter: any;
  export const Routes: any;
  export const Route: any;
  export const Navigate: any;
  export const NavLink: any;
  export const Link: any;
  export const useNavigate: any;
  export const useSearchParams: any;
}

declare module "@testing-library/react" {
  export const render: any;
  export const screen: any;
  export const fireEvent: any;
  export const waitFor: any;
  export const act: any;
}

declare module "vitest" {
  export const describe: any;
  export const it: any;
  export const test: any;
  export const expect: any;
  export const vi: any;
  export const beforeEach: any;
  export const afterEach: any;
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
