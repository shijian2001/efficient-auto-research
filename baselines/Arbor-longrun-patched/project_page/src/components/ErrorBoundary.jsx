import { Component } from 'react';

/**
 * Renders `fallback` (default: nothing) if a child throws — used to wrap the
 * WebGL Threads background so a missing/failed WebGL context never blanks the page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    if (import.meta.env.DEV) console.warn('[Arbor] background disabled:', error?.message);
  }

  render() {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
