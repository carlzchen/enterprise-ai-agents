package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface EcnLineRepository extends JpaRepository<EcnLine, Long> {
    List<EcnLine> findByEcnId(Long ecnId);
    void deleteByEcnId(Long ecnId);
}
