import { useState } from 'react'

import { login } from '../api/client'

interface Props {
  onSignedIn: () => void
  /** Set when a live session expired rather than the user arriving cold. */
  expired?: boolean
}

export function SignIn({ onSignedIn, expired }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(password)
      setPassword('')
      onSignedIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={submit}>
        <h1>Trading Intelligence</h1>
        <p className="muted">
          {expired
            ? 'Your session expired. Sign in to continue.'
            : 'Sign in to view live prices, news, and your portfolio.'}
        </p>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Password"
          aria-label="Password"
          autoFocus
          autoComplete="current-password"
        />
        <button type="submit" disabled={busy || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  )
}
