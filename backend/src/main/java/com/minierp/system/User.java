package com.minierp.system;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "sys_user")
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String username;
    private String passwordHash;
    private String displayName;
    private String role;
    private String dept;
    private boolean enabled = true;
    /** 统一身份：账号对应的员工，员工离职时账号自动停用 */
    private Long employeeId;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
