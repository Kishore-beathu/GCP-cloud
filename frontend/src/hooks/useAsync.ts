// Minimal data-fetching hook: loading / error / data plus manual reload.

import { useCallback, useEffect, useRef, useState } from 'react'

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const generation = useRef(0)

  const run = useCallback(() => {
    const current = ++generation.current
    setLoading(true)
    setError(null)
    loader()
      .then((result) => {
        if (generation.current === current) setData(result)
      })
      .catch((err: unknown) => {
        if (generation.current === current) {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
      .finally(() => {
        if (generation.current === current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(run, [run])

  return { data, error, loading, reload: run }
}
