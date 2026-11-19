package com.minierp.common;

import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;
import io.swagger.v3.oas.models.tags.Tag;
import org.springdoc.core.models.GroupedOpenApi;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * OpenAPI 文档：/swagger-ui.html（交互文档）、/v3/api-docs（机器可读，供其他系统 / MCP 网关对接）。
 * 所有 /api/** 接口使用 JWT：先调 /api/auth/login 取 token，再以 Authorization: Bearer 调用。
 */
@Configuration
public class OpenApiConfig {
    @Bean
    public OpenAPI minierpOpenApi() {
        return new OpenAPI()
                .info(new Info().title("MiniERP API").version("1.0")
                        .description("""
                                微特电机制造企业一体化业务系统开放接口。
                                - 认证：POST /api/auth/login 获取 JWT，之后请求头带 Authorization: Bearer <token>，权限按用户角色（RBAC）。
                                - 追踪：可传 X-Trace-Id 请求头串联跨系统调用，响应头会返回同一个追踪号；错误响应体包含 traceId。
                                - 通用查询：GET /api/views/{key}?page=&size=&sort=&<过滤列>=&dateFrom=&dateTo=，可用 key 见 GET /api/views（视图目录）。
                                - 写接口全部记审计日志。"""))
                .components(new Components().addSecuritySchemes("bearer",
                        new SecurityScheme().type(SecurityScheme.Type.HTTP).scheme("bearer").bearerFormat("JWT")))
                .addSecurityItem(new SecurityRequirement().addList("bearer"))
                .addTagsItem(new Tag().name("views").description("通用只读查询（白名单视图）"));
    }

    @Bean
    public GroupedOpenApi allApis() {
        return GroupedOpenApi.builder().group("minierp").pathsToMatch("/api/**").build();
    }
}
