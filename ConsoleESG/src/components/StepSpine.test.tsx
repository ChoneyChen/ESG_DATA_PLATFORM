import {describe, expect, it} from 'vitest';
import {render, screen} from '@testing-library/react';
import {StepSpine, stageOf} from './StepSpine';

describe('生产主线', () => {
  it('把每个入口映射到链路上的一步', () => {
    expect(stageOf('reports')).toBe('reports');
    // 读取是提取内部的一步，不再单独占一个入口。
    expect(stageOf('ocr')).toBe('extraction');
    expect(stageOf('processing')).toBe('extraction');
    expect(stageOf('extractions')).toBe('extraction');
    expect(stageOf('automation')).toBe('extraction');
    expect(stageOf('runtime')).toBe('extraction');
    expect(stageOf('review')).toBe('review');
    expect(stageOf('publication')).toBe('published');
  });

  it('当前步骤是唯一标记为 current 的项，之前步骤标记完成', () => {
    render(<StepSpine stage="extraction"/>);
    const steps = screen.getAllByRole('listitem');
    expect(steps.map(step => step.className.includes('current'))).toEqual([false, true, false, false]);
    expect(steps.filter(step => step.className.includes('done'))).toHaveLength(1);
    expect(screen.queryByText('读取')).toBeNull();
  });

  it('下一步是一个明确动作，而不是又一组菜单', () => {
    render(<StepSpine stage="extraction" next={{label: '提取本次指标'}}/>);
    expect(screen.getByRole('button', {name: '提取本次指标 →'})).toBeTruthy();
  });
});
