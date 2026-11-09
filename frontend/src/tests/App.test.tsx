import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('App component', () => {
  it('redirects unauthenticated users to sign in', async () => {
    render(<App />)
    const header = await screen.findByRole('heading', { name: /sign in/i })
    expect(header).toBeTruthy()
  })
})
