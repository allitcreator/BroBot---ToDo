import { Component, ReactNode } from "react";

// Сбой отрисовки одного материала не должен давать белый экран: остаётся
// текст и кнопки действий (удалить битый материал).
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) return <p className="ritual-note">Не удалось отрисовать</p>;
    return this.props.children;
  }
}
