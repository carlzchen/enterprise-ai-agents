package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface ProjectGateRepository extends JpaRepository<ProjectGate, Long> {
    List<ProjectGate> findByProjectIdOrderById(Long projectId);
}
