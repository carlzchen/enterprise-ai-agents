package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface BomRevisionRepository extends JpaRepository<BomRevision, Long> {
    Optional<BomRevision> findByProductIdAndStatus(Long productId, String status);
    boolean existsByProductId(Long productId);
}
