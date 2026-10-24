import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('App component', () => {
  it('renders learn React link', () => {
    render(<App />)
    const header = screen.getByRole('heading', { name: /welcome to ai workspace frontend/i })
    expect(header).toBeInTheDocument()
  })
})