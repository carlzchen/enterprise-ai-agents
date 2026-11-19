package com.minierp.workflow;

import java.time.LocalDateTime;

/**
 * 流程结束事件（同步、同一事务内发布）：业务模块监听自己的 defCode，完成后续动作
 * （报销入账、请假写考勤、离职登记、工资入账……）。监听器抛异常会让审批操作整体回滚，保证审批结果与业务状态一致。
 *
 * @param status APPROVED / REJECTED / WITHDRAWN
 */
public record WorkflowFinishedEvent(long instanceId, String defCode, String bizType, Long bizId, String status,
                                    long applicantEmpId, Long lastApproverEmpId, LocalDateTime finishedAt) {
}
