declare module 'plotly.js-dist-min' {
  const Plotly: {
    newPlot: (
      root: HTMLElement,
      data: unknown[],
      layout?: unknown,
      config?: unknown,
    ) => Promise<unknown>
    react: (
      root: HTMLElement,
      data: unknown[],
      layout?: unknown,
      config?: unknown,
    ) => Promise<unknown>
    purge: (root: HTMLElement) => void
    Plots: { resize: (root: HTMLElement) => void }
    relayout: (
      root: HTMLElement,
      update: Record<string, unknown>,
    ) => Promise<unknown>
    downloadImage: (
      root: HTMLElement,
      options: Record<string, unknown>,
    ) => Promise<string>
  }
  export default Plotly
}
