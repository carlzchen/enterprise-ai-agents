package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface BomRevisionLineRepository extends JpaRepository<BomRevisionLine, Long> {
    List<BomRevisionLine> findByRevisionId(Long revisionId);
}
